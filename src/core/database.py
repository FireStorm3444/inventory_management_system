from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import ORMExecuteState, Session, UOWTransaction, with_loader_criteria
from sqlmodel import SQLModel
from src.core.config import settings
from src.core.models import TenantBase
from src.core.security.context import (
    SecurityViolationError,
    get_tenant_id,
    is_system_bypass_active,
)

# High-performance asynchronous connection pool
engine: AsyncEngine = create_async_engine(
    settings.async_database_url,
    echo=(settings.ENVIRONMENT == "development"),
    pool_size=20,
    max_overflow=10,
    pool_pre_ping=True,
)

# Use the modern 2.0 async session factory
AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ==========================================
# COMPONENT 3: READ INTERCEPTOR (do_orm_execute)
# ==========================================
@event.listens_for(Session, "do_orm_execute")
def receive_do_orm_execute(orm_execute_state: ORMExecuteState) -> None:
    """Intercept outgoing SQL statements to dynamically inject multi-tenant isolation criteria."""
    if is_system_bypass_active():
        return

    # Apply criteria to SELECT, UPDATE, and DELETE.
    # Note: We removed the `is_relationship_load` guard so that lazy-loaded queries
    # trigger this hook and get properly filtered too!
    if (
        orm_execute_state.is_select or orm_execute_state.is_update or orm_execute_state.is_delete
    ) and not orm_execute_state.is_column_load:
        active_tenant_id = get_tenant_id()  # Fail-closed if context is missing
        options = []

        # Iterate over all concrete table mappers involved in this specific query
        for mapper in orm_execute_state.all_mappers:
            if issubclass(mapper.class_, TenantBase):
                options.append(
                    with_loader_criteria(
                        mapper.class_,  # Pass the concrete table (e.g., StockLocation) safely
                        lambda cls: cls.tenant_id == active_tenant_id,
                        include_aliases=True,
                    )
                )

        # Inject all dynamically generated isolation criteria into the statement
        if options:
            orm_execute_state.statement = orm_execute_state.statement.options(*options)


# ==========================================
# COMPONENT 4: WRITE INTERCEPTOR (before_flush)
# ==========================================
@event.listens_for(Session, "before_flush")
def receive_before_flush(session: Session, flush_context: UOWTransaction, instances: Any) -> None:
    """Enforce fail-closed tenant_id assignment and prevent tampering prior to PostgreSQL writes."""
    if is_system_bypass_active():
        return

    active_tenant_id = get_tenant_id()

    # 1. Inspect new rows: Auto-assign tenant_id or block explicit cross-tenant insertion attempts
    for instance in session.new:
        if isinstance(instance, TenantBase):
            current_id = getattr(instance, "tenant_id", None)
            if current_id is None or not str(current_id).strip():
                instance.tenant_id = active_tenant_id
            elif instance.tenant_id != active_tenant_id:
                raise SecurityViolationError(
                    f"SECURITY VIOLATION: Cross-tenant INSERT blocked! Attempted to assign "
                    f"tenant_id='{instance.tenant_id}' under active context '{active_tenant_id}'."
                )

    # 2. Inspect dirty rows: Prevent modifying tenant_id or updating rows belonging to other tenants
    for instance in session.dirty:
        if isinstance(instance, TenantBase):
            history = inspect(instance).attrs.tenant_id.history
            if history.has_changes():
                raise SecurityViolationError(
                    f"SECURITY VIOLATION: Tenant ID tampering detected on UPDATE for {instance.__class__.__name__}! "
                    f"Attempted modification from '{history.deleted}' to '{history.added}'."
                )
            if instance.tenant_id != active_tenant_id:
                raise SecurityViolationError(
                    f"SECURITY VIOLATION: Cross-tenant UPDATE blocked! Entity belongs to "
                    f"'{instance.tenant_id}' but active pipeline context is '{active_tenant_id}'."
                )


async def init_db() -> None:
    """Bootstrap tables on startup (For development only; Alembic handles production)."""
    async with engine.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)


async def get_db() -> AsyncGenerator[AsyncSession]:
    """FastAPI Dependency: Yields an isolated database session per request."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
