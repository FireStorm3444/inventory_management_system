import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token

logger = logging.getLogger(__name__)

# ==========================================
# THREAD-SAFE / EVENT-LOOP-SAFE CONTEXT STORAGE
# ==========================================
# Holds the active organization tenant_id for the current asyncio task.
_tenant_context: ContextVar[str | None] = ContextVar("tenant_id", default=None)

# Holds the security flag indicating if the system escape hatch is active.
_system_bypass_context: ContextVar[bool] = ContextVar("system_bypass", default=False)


class SecurityViolationError(RuntimeError):
    """Raised when data access is attempted without a valid tenant scoping context."""

    pass


# ==========================================
# TENANT GETTERS & SETTERS
# ==========================================
def set_tenant_id(tenant_id: str) -> Token[str | None]:
    """Set the active organization tenant identifier for the current async execution pipeline.

    Returns a ContextVar Token that MUST be used to reset the context when the request ends.
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.warning("Invalid tenant ID: %s", tenant_id)
        raise ValueError("Tenant ID must be a non-empty string.")
    return _tenant_context.set(tenant_id.strip())


def get_tenant_id() -> str:
    """Retrieve the active tenant_id.

    FAIL-CLOSED GUARANTEE: Raises SecurityViolationError if accessed outside an active context
    unless the explicit system bypass hatch is currently open.
    """
    if is_system_bypass_active():
        raise SecurityViolationError(
            "CRITICAL: get_tenant_id() called while system bypass is active. "
            "System operations must explicitly query across tenants or specify targeted IDs."
        )

    tenant_id = _tenant_context.get()
    if tenant_id is None:
        logger.warning("No tenant context available.")
        raise SecurityViolationError(
            "SECURITY VIOLATION: Attempted to access tenant-scoped data without an active "
            "tenant context. Check middleware execution order or authentication payload."
        )
    return tenant_id


def get_tenant_id_silent() -> str | None:
    """Safely inspect the active tenant_id without throwing an exception.

    Useful for diagnostic logging, middleware inspection, or non-scoped utilities.
    """
    return _tenant_context.get()


def reset_tenant_id(token: Token[str | None]) -> None:
    """Restore the previous tenant context state using the token issued by set_tenant_id()."""
    _tenant_context.reset(token)


# ==========================================
# SYSTEM ESCAPE HATCH (AUDITED BYPASS)
# ==========================================
def is_system_bypass_active() -> bool:
    """Check if the current async task is operating under administrative system bypass."""
    return _system_bypass_context.get()


@contextmanager
def system_bypass_tenant(reason: str) -> Iterator[None]:
    """Explicit context manager to temporarily suspend tenant isolation for system tasks.

    MUST be accompanied by a documented engineering reason (e.g., 'Alembic Schema Migration',
    'Nightly Global Analytics Aggregation'). Every invocation emits structured telemetry.
    """
    if not reason or len(reason.strip()) < 5:
        raise ValueError("System bypass requires a detailed engineering audit justification.")

    logger.warning(
        "SYSTEM_BYPASS_ENGAGED | caller_reason: %s | previous_tenant: %s",
        reason,
        _tenant_context.get(),
    )

    token = _system_bypass_context.set(True)
    try:
        yield
    finally:
        _system_bypass_context.reset(token)
        logger.warning("SYSTEM_BYPASS_TERMINATED | caller_reason: %s", reason)
