import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col
from src.domains.catalog.models import Product

import rust_engine as _rust_engine

rust_engine: Any = _rust_engine


logger = logging.getLogger(__name__)

# 1. Global Singleton of the Rust Engine
# This mounts the bare-metal graph to the FastAPI worker's memory space
discovery_engine = rust_engine.CatalogDiscoveryEngine()


async def hydrate_catalog_graph(session: AsyncSession) -> None:
    """
    Phase 3 Bootloader: Fetches all active SKUs across the platform
    and streams them into the bare-metal Rust graph.
    """
    logger.info("HYDRATION_START | Booting Rust Discovery Engine...")

    # Execute a system-level read to pull all active products.
    stmt = select(
        col(Product.id),
        col(Product.tenant_id),
        col(Product.sku),
        col(Product.name),
        col(Product.price),
    ).where(col(Product.is_active).is_(True))

    # 2. Asynchronous Server-Side Streaming (Yields 10k rows at a time)
    result = await session.stream(stmt)

    count = 0
    # yield_per prevents Python memory spikes on massive catalogs
    async for row in result.yield_per(10000):
        p_id, tenant_id, sku, name, price = row

        # 3. Initialize the Rust C-Extension DTO
        dto = rust_engine.CatalogSearchResult(str(p_id), sku, name, float(price))

        # 4. Dual-Indexing: Allow warehouse workers to search by SKU or by Name
        discovery_engine.insert(tenant_id, sku, dto)
        discovery_engine.insert(tenant_id, name, dto)
        count += 1

    logger.info("HYDRATION_COMPLETE | [%d] SKUs indexed in bare-metal memory.", count)
