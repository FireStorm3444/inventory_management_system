import json
import logging
from typing import Any

from fastapi import Request, status
from fastapi.responses import JSONResponse, Response

logger = logging.getLogger(__name__)


def is_htmx_request(request: Request) -> bool:
    """Detect if the incoming request was triggered by an HTMX frontend."""
    return request.headers.get("HX-Request") == "true"


def htmx_toast_response(message: str, level: str = "error") -> Response:
    """Return a DOM-preserving response that triggers an Alpine.js toast notification.

    HTMX silently drops 4xx/5xx responses by default. To guarantee the UI toast renders,
    we return a 200 OK but explicitly command HTMX NOT to swap any HTML via HX-Reswap.
    """
    trigger_data: dict[str, dict[str, Any]] = {
        "show-toast": {
            "level": level,
            "message": message,
        }
    }
    return Response(
        status_code=status.HTTP_200_OK,
        headers={
            "HX-Trigger": json.dumps(trigger_data),
            "HX-Reswap": "none",
        },
    )


# Type 'exc' as Exception to satisfy Starlette's strict ExceptionHandler protocol
async def security_violation_handler(request: Request, exc: Exception) -> Response:
    """Intercept cross-tenant data leaks and missing contexts."""
    logger.error("SECURITY_VIOLATION | Path: %s | Error: %s", request.url.path, str(exc))

    if is_htmx_request(request):
        return htmx_toast_response(
            message="Security Violation: Unauthorized organization access.", level="error"
        )

    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"error": "SecurityViolation", "message": str(exc)},
    )


# Type 'exc' as Exception to satisfy Starlette's strict ExceptionHandler protocol
async def inventory_domain_handler(request: Request, exc: Exception) -> Response:
    """Intercept stock engine failures (e.g., negative stock, idempotency collisions)."""
    logger.warning("INVENTORY_RULE_FAILED | Path: %s | Error: %s", request.url.path, str(exc))

    if is_htmx_request(request):
        return htmx_toast_response(message=str(exc), level="warning")

    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content={"error": exc.__class__.__name__, "message": str(exc)},
    )


async def global_exception_handler(request: Request, exc: Exception) -> Response:
    """Catch-all for unhandled server crashes to prevent leaking stack traces to the UI."""
    logger.exception("UNHANDLED_SERVER_ERROR | Path: %s", request.url.path)

    if is_htmx_request(request):
        return htmx_toast_response(
            message="An unexpected system error occurred. Our engineers have been notified.",
            level="error",
        )

    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"error": "InternalServerError", "message": "An unexpected error occurred."},
    )
