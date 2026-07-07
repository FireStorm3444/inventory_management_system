import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import CheckConstraint, DateTime, UniqueConstraint
from sqlmodel import Field
from src.core.models import TenantBase


def get_utc_now() -> datetime:
    """Return timezone-aware UTC timestamps compatible with Python 3.14."""
    return datetime.now(UTC)


class StockLocation(TenantBase, table=True):
    """Physical or logical storage bin/warehouse within a tenant organization."""

    __tablename__: ClassVar[str] = "stock_locations"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    name: str = Field(index=True, max_length=100)
    code: str = Field(index=True, max_length=30)  # e.g., 'WH-MAIN', 'BIN-A1'
    is_default: bool = Field(default=False)
    is_sellable: bool = Field(default=True)  # False for 'Damaged/Returns' bins
    created_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )

    __table_args__: ClassVar[tuple[Any, ...]] = (
        UniqueConstraint("tenant_id", "code", name="uq_tenant_location_code"),
    )


class StockBalance(TenantBase, table=True):
    """Materialized summary cache and target for row-level SELECT ... FOR UPDATE locks."""

    __tablename__: ClassVar[str] = "stock_balances"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    product_id: uuid.UUID = Field(foreign_key="products.id", index=True)
    location_id: uuid.UUID = Field(foreign_key="stock_locations.id", index=True)

    # Hard physical stock sitting on the shelf
    physical_qty: int = Field(default=0)
    # Stock held in customer carts / draft invoices waiting for checkout
    reserved_qty: int = Field(default=0)

    updated_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )

    # Database-level guarantees: Hard system abort if quantities go below 0 or over-reserve
    __table_args__: ClassVar[tuple[Any, ...]] = (
        UniqueConstraint("tenant_id", "product_id", "location_id", name="uq_tenant_prod_loc"),
        CheckConstraint("physical_qty >= 0", name="ck_physical_qty_non_negative"),
        CheckConstraint("reserved_qty >= 0", name="ck_reserved_qty_non_negative"),
        CheckConstraint("physical_qty >= reserved_qty", name="ck_no_over_reservation"),
    )

    @property
    def available_qty(self) -> int:
        """Dynamic calculation of stock currently available for new customer sales."""
        return self.physical_qty - self.reserved_qty


class StockLedger(TenantBase, table=True):
    """Immutable audit log of all inventory movements. Source of truth."""

    __tablename__: ClassVar[str] = "stock_ledgers"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    product_id: uuid.UUID = Field(foreign_key="products.id", index=True)
    location_id: uuid.UUID = Field(foreign_key="stock_locations.id", index=True)

    # Movement values (+50 Purchase, -1 Sale, etc.)
    physical_delta: int = Field(default=0)
    reserved_delta: int = Field(default=0)

    # Reason code: 'PURCHASE_RECEIPT', 'SALE_CHECKOUT', 'CART_RESERVE', 'CART_RELEASE', 'DAMAGE'
    reason: str = Field(index=True, max_length=50)
    reference_id: str | None = Field(default=None, index=True)  # Order ID / PO Number
    idempotency_key: str = Field(index=True, max_length=100)  # Prevents network double-deductions

    created_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )

    __table_args__: ClassVar[tuple[Any, ...]] = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_tenant_idempotency"),
    )
