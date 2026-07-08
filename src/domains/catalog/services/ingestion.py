import asyncio
import logging
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime  # <-- NEW IMPORT
from typing import Any

import polars as pl
from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select
from src.domains.catalog.models import Category

logger = logging.getLogger(__name__)


async def _sync_categories_to_db(
    session: AsyncSession, tenant_id: str, category_names: set[str]
) -> dict[str, uuid.UUID]:
    """I/O Bound: Queries the database for existing categories and provisions missing ones."""
    if not category_names:
        return {}

    stmt = select(col(Category.id), col(Category.name)).where(
        col(Category.tenant_id) == tenant_id, col(Category.name).in_(category_names)
    )
    result = await session.execute(stmt)
    existing_cats = {name: cat_id for cat_id, name in result.all()}

    missing_names = category_names - set(existing_cats.keys())
    if missing_names:
        new_cats = [
            {"id": uuid.uuid4(), "tenant_id": tenant_id, "name": name} for name in missing_names
        ]
        insert_stmt = (
            insert(Category).values(new_cats).returning(col(Category.id), col(Category.name))
        )
        new_result = await session.execute(insert_stmt)

        for cat_id, name in new_result.all():
            existing_cats[name] = cat_id

    return existing_cats


def _process_polars_chunk(
    df: pl.DataFrame, category_map: dict[str, uuid.UUID], tenant_id: str
) -> tuple[list[tuple[Any, ...]], list[dict[str, Any]]]:
    """CPU Bound (Rust): Executes vectorized mappings and business invariant validation."""
    # (Column rename moved to the generator to satisfy early lambda execution)

    mapping_df = pl.DataFrame(
        {"category": list(category_map.keys()), "category_id": list(category_map.values())},
        schema={"category": pl.String, "category_id": pl.Object},
    )
    df = df.join(mapping_df, on="category", how="left")

    total_rows = len(df)
    now = datetime.now(UTC)

    # FIX 2: Explicitly provide ORM defaults to bypass PostgreSQL NOT NULL constraints
    df = df.with_columns(
        [
            pl.lit(tenant_id).alias("tenant_id"),
            pl.Series("id", [uuid.uuid4() for _ in range(total_rows)], dtype=pl.Object),
            pl.col("price").cast(pl.Float64, strict=False),
            pl.lit(True).alias("is_active"),
            pl.lit(now).alias("created_at"),
            pl.lit(now).alias("updated_at"),
        ]
    )

    is_sku_valid = pl.col("sku").is_not_null() & (pl.col("sku").str.strip_chars() != "")
    is_price_valid = pl.col("price").is_not_null() & (pl.col("price") > 0)
    is_cat_valid = pl.col("category_id").is_not_null()

    df = df.with_columns(
        [
            pl.when(is_sku_valid)
            .then(pl.lit(""))
            .otherwise(pl.lit("Missing SKU | "))
            .alias("err_sku"),
            pl.when(is_price_valid)
            .then(pl.lit(""))
            .otherwise(pl.lit("Invalid Price | "))
            .alias("err_price"),
            pl.when(is_cat_valid)
            .then(pl.lit(""))
            .otherwise(pl.lit("Category Error | "))
            .alias("err_cat"),
        ]
    )

    df = df.with_columns(
        (pl.col("err_sku") + pl.col("err_price") + pl.col("err_cat")).alias("failure_reason")
    )

    is_valid = pl.col("failure_reason") == ""

    # Include the newly injected defaults into the raw target extraction
    valid_df = df.filter(is_valid).select(
        [
            "id",
            "tenant_id",
            "category_id",
            "sku",
            "name",
            "price",
            "is_active",
            "created_at",
            "updated_at",
        ]
    )
    valid_records = valid_df.rows()
    invalid_records = df.filter(~is_valid).to_dicts()

    return valid_records, invalid_records


def _read_next_batch(reader: Any) -> pl.DataFrame | None:
    """Safely extracts the next chunk from newer Polars iterator protocols."""
    try:
        if hasattr(reader, "next_batches"):
            batches = reader.next_batches(1)
            return batches[0] if batches else None
        return next(reader)
    except StopIteration:
        return None


async def process_catalog_stream(
    file_path: str, tenant_id: str, session: AsyncSession, batch_size: int = 10000
) -> AsyncGenerator[tuple[list[tuple[Any, ...]], list[dict[str, Any]]]]:

    # Modern Polars API usage: The Rust engine now auto-optimizes batch sizes for memory safety
    reader = await asyncio.to_thread(lambda: pl.scan_csv(file_path).collect_batches())

    while True:
        df = await asyncio.to_thread(_read_next_batch, reader)
        if df is None:
            break

        # 1. Lowercase and strip columns
        df = df.rename({col: col.strip().lower() for col in df.columns})

        schema_map = {"item sku": "sku", "product name": "name", "cost": "price"}
        available_mappings = {k: v for k, v in schema_map.items() if k in df.columns}
        df = df.rename(available_mappings)

        # 2. Extract categories
        unique_categories = await asyncio.to_thread(
            lambda current_df: set(current_df["category"].drop_nulls().unique().to_list()), df
        )

        category_map = await _sync_categories_to_db(session, tenant_id, unique_categories)

        valid_records, invalid_records = await asyncio.to_thread(
            _process_polars_chunk, df, category_map, tenant_id
        )

        yield valid_records, invalid_records


async def execute_catalog_ingestion(
    file_path: str, tenant_id: str, session: AsyncSession
) -> tuple[int, int, list[dict[str, Any]]]:

    await session.execute(
        text(
            "CREATE TEMP TABLE catalog_ingest_temp (LIKE products EXCLUDING CONSTRAINTS) ON COMMIT DROP;"
        )
    )

    conn = await session.connection()
    raw_conn = await conn.get_raw_connection()
    asyncpg_conn = raw_conn.driver_connection

    if asyncpg_conn is None:
        raise RuntimeError("CRITICAL: Lost underlying asyncpg driver connection.")

    total_processed = 0
    total_upserted = 0
    all_invalid = []

    async for valid_records, invalid_records in process_catalog_stream(
        file_path, tenant_id, session
    ):
        all_invalid.extend(invalid_records)
        total_processed += len(valid_records) + len(invalid_records)

        if valid_records:
            # Map the new columns to the COPY command
            await asyncpg_conn.copy_records_to_table(
                "catalog_ingest_temp",
                records=valid_records,
                columns=[
                    "id",
                    "tenant_id",
                    "category_id",
                    "sku",
                    "name",
                    "price",
                    "is_active",
                    "created_at",
                    "updated_at",
                ],
            )
            total_upserted += len(valid_records)

    if total_upserted > 0:
        merge_query = """
        INSERT INTO products (id, tenant_id, category_id, sku, name, price, is_active, created_at, updated_at)
        SELECT id, tenant_id, category_id, sku, name, price, is_active, created_at, updated_at FROM catalog_ingest_temp
        ON CONFLICT (sku) DO UPDATE SET
            category_id = EXCLUDED.category_id,
            name = EXCLUDED.name,
            price = EXCLUDED.price,
            is_active = EXCLUDED.is_active,
            updated_at = NOW(); -- Explicitly patching the ORM bypass
        """
        await session.execute(text(merge_query))

    return total_processed, total_upserted, all_invalid
