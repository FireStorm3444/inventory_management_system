import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import ClassVar

from sqlalchemy import DateTime
from sqlmodel import Field
from src.core.models import TenantBase


def get_utc_now() -> datetime:
    """Return timezone-aware UTC timestamps compatible with Python 3.14."""
    return datetime.now(UTC)


class Category(TenantBase, table=True):
    __tablename__: ClassVar[str] = "categories"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    name: str = Field(index=True, max_length=100)
    description: str | None = Field(default=None)
    created_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )


class Product(TenantBase, table=True):
    __tablename__: ClassVar[str] = "products"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True, index=True)
    sku: str = Field(index=True, unique=True, max_length=50)
    name: str = Field(index=True, max_length=255)
    price: Decimal = Field(default=Decimal("0.00"), max_digits=10, decimal_places=2)
    category_id: uuid.UUID | None = Field(default=None, foreign_key="categories.id")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )
    updated_at: datetime = Field(
        default_factory=get_utc_now,
        sa_type=DateTime(timezone=True),
        sa_column_kwargs={"onupdate": get_utc_now},
    )
