import uuid

import pytest
from httpx import AsyncClient
from src.core.security.context import _tenant_context
from src.domains.catalog.services.discovery import discovery_engine

import rust_engine

pytestmark = pytest.mark.asyncio(loop_scope="session")


@pytest.fixture
def dual_tenant_setup() -> tuple[str, str]:
    """Provides two isolated tenants and injects overlapping SKUs directly into Rust memory."""
    tenant_a = str(uuid.uuid4())
    tenant_b = str(uuid.uuid4())

    # Inject into Tenant A
    dto_a = rust_engine.CatalogSearchResult(str(uuid.uuid4()), "SYS-100", "System Board", 500.0)
    discovery_engine.insert(tenant_a, dto_a.sku, dto_a)
    discovery_engine.insert(tenant_a, dto_a.name, dto_a)

    # Inject into Tenant B (Same prefix, different product)
    dto_b = rust_engine.CatalogSearchResult(
        str(uuid.uuid4()), "SYS-999", "System Controller", 999.0
    )
    discovery_engine.insert(tenant_b, dto_b.sku, dto_b)
    discovery_engine.insert(tenant_b, dto_b.name, dto_b)

    return tenant_a, tenant_b


async def test_rust_autocomplete_tenant_isolation(
    client: AsyncClient, dual_tenant_setup: tuple[str, str]
):
    tenant_a, tenant_b = dual_tenant_setup

    # 1. Search as Tenant A
    _tenant_context.set(tenant_a)
    res_a = await client.get("/api/catalog/search?q=SYS", headers={"X-Tenant-ID": tenant_a})

    assert res_a.status_code == 200
    html_a = res_a.text
    assert "SYS-100" in html_a
    assert "SYS-999" not in html_a  # Tenant B's data MUST NOT leak

    # 2. Search as Tenant B
    _tenant_context.set(tenant_b)
    res_b = await client.get("/api/catalog/search?q=System", headers={"X-Tenant-ID": tenant_b})

    assert res_b.status_code == 200
    html_b = res_b.text
    assert "SYS-999" in html_b
    assert "SYS-100" not in html_b

    # 3. Minimum length constraint
    res_short = await client.get("/api/catalog/search?q=S", headers={"X-Tenant-ID": tenant_a})
    assert res_short.text == ""  # Should instantly reject lengths < 2
