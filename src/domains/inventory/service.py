import logging
import uuid
from dataclasses import dataclass

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select
from src.domains.inventory.exceptions import IdempotencyConflictError, InsufficientStockError
from src.domains.inventory.models import StockBalance, StockLedger, StockLocation

logger = logging.getLogger(__name__)


async def get_or_create_default_location(session: AsyncSession) -> StockLocation:
    """Retrieve the default stock location for the active tenant context.

    If none exists, safely initialize 'WH-MAIN' using a PostgreSQL savepoint
    to guarantee idempotency against concurrent onboarding race conditions.
    """
    # 1. Fast path: Query existing default location (Zero-trust hook auto-injects tenant_id)
    stmt = select(StockLocation).where(StockLocation.is_default)
    existing_location = await session.scalar(stmt)

    if existing_location is not None:
        return existing_location

    logger.info("DEFAULT_WAREHOUSE_INIT | Creating initial WH-MAIN for active tenant context.")

    # 2. Slow path: Attempt creation within an isolated PostgreSQL SAVEPOINT
    new_location = StockLocation(
        name="Main Warehouse",
        code="WH-MAIN",
        is_default=True,
        is_sellable=True,
    )

    try:
        # begin_nested() issues a SAVEPOINT to Postgres.
        # If an IntegrityError occurs inside this block, only the savepoint rolls back!
        async with session.begin_nested():
            session.add(new_location)
            await session.flush()

        return new_location

    except IntegrityError as err:
        # 3. Concurrency Collision Recovery:
        # Another concurrent request inserted WH-MAIN exact milliseconds before our commit.
        # The savepoint rolled back cleanly; we now safely fetch and return the winner's row.
        logger.warning("DEFAULT_WAREHOUSE_RACE_DETECTED | Recovering existing WH-MAIN from DB.")
        winner_location = await session.scalar(stmt)

        if winner_location is None:
            raise RuntimeError(
                "CRITICAL: IntegrityError triggered during WH-MAIN creation, "
                "but subsequent SELECT returned None. Check DB table constraints."
            ) from err

        return winner_location


@dataclass(frozen=True, slots=True)
class CartItemDTO:
    """Immutable data transfer object representing a single line item in a checkout request."""

    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: int


async def _get_locked_balance(
    session: AsyncSession, product_id: uuid.UUID, location_id: uuid.UUID
) -> StockBalance:
    """Helper: Retrieve a StockBalance row with an exclusive PostgreSQL row lock (FOR UPDATE).

    If the balance row does not yet exist for this product/location pair, cleanly initialize it.
    """
    stmt = (
        select(StockBalance)
        .where(
            StockBalance.product_id == product_id,
            StockBalance.location_id == location_id,
        )
        .with_for_update()
    )
    balance = await session.scalar(stmt)

    if balance is None:
        # Initialize zero-balance record for newly placed SKUs
        balance = StockBalance(
            product_id=product_id,
            location_id=location_id,
            physical_qty=0,
            reserved_qty=0,
        )
        session.add(balance)
        await session.flush()

    return balance


async def adjust_stock(
    session: AsyncSession,
    *,
    product_id: uuid.UUID,
    location_id: uuid.UUID,
    physical_delta: int = 0,
    reserved_delta: int = 0,
    reason: str,
    reference_id: str | None = None,
    idempotency_key: str,
) -> StockBalance:
    """Execute an atomic, ledger-backed stock adjustment for a single SKU.

    Used for Purchase Order receipts (+qty), damage write-offs (-qty), or soft cart reservations.
    """
    # 1. Check idempotency: If this network request already succeeded, return early
    existing_ledger = await session.scalar(
        select(StockLedger).where(StockLedger.idempotency_key == idempotency_key)
    )
    if existing_ledger is not None:
        raise IdempotencyConflictError(idempotency_key)

    # 2. Acquire row-level lock on the balance target
    balance = await _get_locked_balance(session, product_id, location_id)

    # 3. Domain validation: Prevent over-deduction or invalid reservation states
    new_physical = balance.physical_qty + physical_delta
    new_reserved = balance.reserved_qty + reserved_delta

    if new_physical < 0 or (new_physical - new_reserved) < 0:
        raise InsufficientStockError(product_id, balance.available_qty, abs(physical_delta))

    # 4. Apply mutations
    balance.physical_qty = new_physical
    balance.reserved_qty = new_reserved

    ledger_entry = StockLedger(
        product_id=product_id,
        location_id=location_id,
        physical_delta=physical_delta,
        reserved_delta=reserved_delta,
        reason=reason,
        reference_id=reference_id,
        idempotency_key=idempotency_key,
    )
    session.add(ledger_entry)

    try:
        await session.flush()
    except IntegrityError as exc:
        # Handle race condition where two workers hit the idempotency check simultaneously
        raise IdempotencyConflictError(idempotency_key) from exc

    return balance


async def checkout_cart(
    session: AsyncSession,
    *,
    items: list[CartItemDTO],
    reference_id: str,
    idempotency_key: str,
    from_reserved: bool = False,
) -> list[StockBalance]:
    """Execute a multi-item atomic checkout.

    Guarantees zero deadlocks via deterministic sorting and ACID compliance across all line items.
    """
    if not items:
        return []

    # 1. Global Idempotency Check
    existing_ledger = await session.scalar(
        select(StockLedger).where(StockLedger.idempotency_key == f"{idempotency_key}_line_0")
    )
    if existing_ledger is not None:
        raise IdempotencyConflictError(idempotency_key)

    # 2. DEADLOCK PREVENTION: Sort items deterministically by (location_id, product_id)
    sorted_items = sorted(items, key=lambda item: (str(item.location_id), str(item.product_id)))

    updated_balances: list[StockBalance] = []

    # 3. Iterate sequentially through sorted items, locking rows in consistent order
    for idx, item in enumerate(sorted_items):
        if item.quantity <= 0:
            continue

        balance = await _get_locked_balance(session, item.product_id, item.location_id)

        line_idempotency = f"{idempotency_key}_line_{idx}"

        if from_reserved:
            # Converting a soft-reserved cart into a finalized sale
            if balance.reserved_qty < item.quantity or balance.physical_qty < item.quantity:
                raise InsufficientStockError(item.product_id, balance.reserved_qty, item.quantity)

            balance.physical_qty -= item.quantity
            balance.reserved_qty -= item.quantity
            physical_delta = -item.quantity
            reserved_delta = -item.quantity
        else:
            # Direct immediate checkout (e.g., POS counter sale)
            if balance.available_qty < item.quantity:
                raise InsufficientStockError(item.product_id, balance.available_qty, item.quantity)

            balance.physical_qty -= item.quantity
            physical_delta = -item.quantity
            reserved_delta = 0

        ledger_entry = StockLedger(
            product_id=item.product_id,
            location_id=item.location_id,
            physical_delta=physical_delta,
            reserved_delta=reserved_delta,
            reason="SALE_CHECKOUT",
            reference_id=reference_id,
            idempotency_key=line_idempotency,
        )
        session.add(ledger_entry)
        updated_balances.append(balance)

    await session.flush()
    return updated_balances
