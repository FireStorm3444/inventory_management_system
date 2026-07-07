from sqlmodel import Field, SQLModel
from src.core.security.context import get_tenant_id


class TenantBase(SQLModel):
    """Base model enforcing multi-tenant isolation across all SaaS entities."""

    tenant_id: str = Field(
        default_factory=get_tenant_id,
        index=True,
        nullable=False,
        description="Organization / Store Tenant Identifier",
    )
