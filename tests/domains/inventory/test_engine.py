import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.security.context import reset_tenant_id, set_tenant_id

# Import the Product model to satisfy the Foreign Key constraint
from src.domains.catalog.models import Product
from src.domains.inventory.exceptions import InsufficientStockError
from src.domains.inventory.service import adjust_stock, get_or_create_default_location


@pytest.mark.asyncio
async def test_negative_stock_prevention(db_session: AsyncSession) -> None:
    """Verify that adjusting stock below zero raises the correct domain exception."""

    # 1. Establish tenant security context for the test
    token = set_tenant_id("tenant_unit_test")

    try:
        # 2. Get the default warehouse location
        location = await get_or_create_default_location(db_session)

        # Create a real product in the database so the StockBalance FK constraint passes
        product = Product(sku="TEST-SKU-001", name="Test Widget", price=9.99)
        db_session.add(product)
        await db_session.flush()

        product_id = product.id  # Use the real database UUID!

        # 3. Simulate receiving 10 items via Purchase Order
        balance = await adjust_stock(
            session=db_session,
            product_id=product_id,
            location_id=location.id,
            physical_delta=10,
            reason="INITIAL_RECEIPT",
            idempotency_key="tx_001",
        )
        assert balance.physical_qty == 10
        assert balance.tenant_id == "tenant_unit_test"

        # 4. Simulate a cashier trying to checkout 15 items (which should fail)
        with pytest.raises(InsufficientStockError) as exc_info:
            await adjust_stock(
                session=db_session,
                product_id=product_id,
                location_id=location.id,
                physical_delta=-15,
                reason="POS_SALE",
                idempotency_key="tx_002",
            )

        # Verify the exception details are precisely correct
        assert exc_info.value.available == 10
        assert exc_info.value.requested == 15

        # Verify the database strictly prevented the transaction
        assert balance.physical_qty == 10

    finally:
        reset_tenant_id(token)
