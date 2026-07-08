import io
import uuid
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from src.core.security.context import _tenant_context
from src.domains.catalog.models import Category, Product

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def test_tenant_id() -> str:
    """Provides an isolated tenant ID and satisfies the database security flush hooks."""
    t_id = str(uuid.uuid4())
    # FIX 1: Manually set the context variable so direct db_session.add() succeeds
    _tenant_context.set(t_id)
    return t_id


async def test_catalog_ingestion_happy_path(
    client: AsyncClient, db_session: AsyncSession, test_tenant_id: str
):
    """
    Validates that a clean CSV correctly provisions missing categories
    and inserts products via the asyncpg COPY protocol.
    """
    existing_category_id = uuid.uuid4()
    existing_cat = Category(id=existing_category_id, tenant_id=test_tenant_id, name="Power Tools")
    db_session.add(existing_cat)
    await db_session.commit()

    csv_content = (
        "Item SKU,Product Name,Cost,Category\n"
        "DRILL-01,Cordless Drill,150.50,Power Tools\n"
        "HAM-01,Claw Hammer,25.00,Hand Tools\n"
    )

    file_payload = {"file": ("catalog.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")}

    # FIX 2: Inject X-Tenant-ID to pass the middleware
    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Tenant-ID": test_tenant_id}

    response = await client.post("/api/catalog/upload", files=file_payload, headers=headers)

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "success"
    assert data["total_processed"] == 2
    assert data["total_upserted"] == 2
    assert data["invalid_rows"] == 0

    await db_session.refresh(existing_cat)

    cat_stmt = select(col(Category.id)).where(col(Category.name) == "Hand Tools")
    new_cat_id = await db_session.scalar(cat_stmt)
    assert new_cat_id is not None

    prod_stmt = select(col(Product.sku)).where(col(Product.tenant_id) == test_tenant_id)
    result = await db_session.execute(prod_stmt)
    skus = result.scalars().all()
    assert "DRILL-01" in skus
    assert "HAM-01" in skus


async def test_catalog_ingestion_edge_cases_and_audit_log(
    client: AsyncClient, db_session: AsyncSession, test_tenant_id: str
):
    """
    Proves the Polars engine correctly vectorizes business rules, filters toxic rows,
    and returns a structured error log without halting the valid inserts.
    """
    csv_content = (
        "sku,name,price,category\n"
        "VALID-01,Good Item,10.0,Misc\n"
        ",Missing SKU,15.0,Misc\n"
        "NEG-01,Bad Price,-5.0,Misc\n"
        "STR-01,String Price,twenty,Misc\n"
    )

    file_payload = {
        "file": ("toxic_catalog.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")
    }

    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Tenant-ID": test_tenant_id}

    response = await client.post("/api/catalog/upload", files=file_payload, headers=headers)

    assert response.status_code == 200
    data = response.json()

    assert data["total_processed"] == 4
    assert data["total_upserted"] == 1
    assert data["invalid_rows"] == 3

    errors = data["errors"]
    failed_skus = [err["sku"] for err in errors]
    assert None in failed_skus
    assert "NEG-01" in failed_skus
    assert "STR-01" in failed_skus

    str_err = next(err for err in errors if err["sku"] == "STR-01")
    assert "Invalid Price" in str_err["failure_reason"]


async def test_catalog_upsert_collision_handling(
    client: AsyncClient, db_session: AsyncSession, test_tenant_id: str
):
    """
    Ensures that uploading a CSV with an existing SKU cleanly updates
    the existing row instead of crashing with a PostgreSQL UniqueViolation.
    """
    cat_id = uuid.uuid4()
    db_session.add(Category(id=cat_id, tenant_id=test_tenant_id, name="Test Cat"))

    existing_product = Product(
        id=uuid.uuid4(),
        tenant_id=test_tenant_id,
        category_id=cat_id,
        sku="UPDATE-ME",
        name="Old Name",
        price=10.00,
    )
    db_session.add(existing_product)
    await db_session.commit()

    csv_content = "sku,name,price,category\nUPDATE-ME,New Brand Name,99.99,Test Cat\n"

    response = await client.post(
        "/api/catalog/upload",
        files={"file": ("update.csv", io.BytesIO(csv_content.encode("utf-8")), "text/csv")},
        headers={"Idempotency-Key": str(uuid.uuid4()), "X-Tenant-ID": test_tenant_id},
    )

    assert response.status_code == 200

    await db_session.refresh(existing_product)
    assert existing_product.name == "New Brand Name"
    assert existing_product.price == Decimal("99.99")


async def test_ingestion_network_perimeter(client: AsyncClient, test_tenant_id: str):
    """
    Validates Path A constraints (Idempotency and File Extension protection)
    are strictly enforced before parsing begins.
    """
    # Missing Idempotency Key but valid tenant
    res_no_key = await client.post("/api/catalog/upload", headers={"X-Tenant-ID": test_tenant_id})
    assert res_no_key.status_code == 422

    # Invalid File Type
    headers = {"Idempotency-Key": str(uuid.uuid4()), "X-Tenant-ID": test_tenant_id}
    res_bad_file = await client.post(
        "/api/catalog/upload",
        files={"file": ("image.png", io.BytesIO(b"fake_image_bytes"), "image/png")},
        headers=headers,
    )
    assert res_bad_file.status_code == 400
    assert "CSV files are accepted" in res_bad_file.json()["detail"]
