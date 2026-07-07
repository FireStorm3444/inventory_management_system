import logging
import uuid

from fastapi import Header, HTTPException, status

logger = logging.getLogger(__name__)


def get_idempotency_key(
    idempotency_key: str = Header(
        ...,  # The ellipsis makes this header strictly required
        alias="Idempotency-Key",
        description="Client-generated UUIDv4 key to prevent duplicate processing of network retries.",
    ),
) -> str:
    """FastAPI Dependency to strictly enforce and validate the Idempotency-Key header."""
    try:
        # Strictly validate that the key is a valid UUIDv4 string.
        # This prevents malformed data or injection attempts from reaching the domain layer.
        parsed_uuid = uuid.UUID(idempotency_key, version=4)
        return str(parsed_uuid)

    except ValueError as err:
        logger.warning("MALFORMED_IDEMPOTENCY_KEY | Received: %s", idempotency_key)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The 'Idempotency-Key' header must be a valid, client-generated UUIDv4 string.",
        ) from err
