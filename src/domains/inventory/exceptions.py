import uuid


class InventoryDomainError(Exception):
    """Base exception for all inventory-related business failures."""

    pass


class InsufficientStockError(InventoryDomainError):
    """Raised when an operation attempts to deduct more stock than available."""

    def __init__(self, product_id: uuid.UUID, available: int, requested: int) -> None:
        self.product_id = product_id
        self.available = available
        self.requested = requested
        super().__init__(
            f"Insufficient available stock for product '{product_id}'. "
            f"Available: {available}, Requested: {requested}."
        )


class IdempotencyConflictError(InventoryDomainError):
    """Raised when a network retry attempts to process an already completed transaction."""

    def __init__(self, idempotency_key: str) -> None:
        self.idempotency_key = idempotency_key
        super().__init__(f"Transaction with idempotency key '{idempotency_key}' already processed.")
