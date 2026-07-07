import logging
from collections.abc import Awaitable, Callable
from typing import override

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from src.core.security.context import reset_tenant_id, set_tenant_id
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)

# Endpoints that are safe to execute without tenant isolation
EXEMPT_PATHS: set[str] = {
    "/",
    "/api/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/favicon.ico",
}


class TenantScopingMiddleware(BaseHTTPMiddleware):
    """FastAPI HTTP Gatekeeper enforcing multi-tenant context isolation.

    Extracts X-Tenant-ID from headers, initializes the async ContextVar,
    and guarantees fail-closed cleanup when the request terminates.
    """

    @override
    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        path = request.url.path

        # 1. Allow exempt infrastructure paths and static files through immediately
        if path in EXEMPT_PATHS or path.startswith("/static"):
            return await call_next(request)

        # 2. Extract Tenant Identifier (Header prioritizes over future JWT claim extraction)
        tenant_id = request.headers.get("X-Tenant-ID")

        if not tenant_id or not tenant_id.strip():
            logger.warning(
                "UNAUTHORIZED_ACCESS_ATTEMPT | path: %s | client: %s",
                path,
                request.client.host if request.client else "unknown",
            )

            # HTMX SPA Protocol: Return out-of-band HTML error banner instead of raw JSON
            if request.headers.get("HX-Request") == "true":
                return HTMLResponse(
                    content="""
                    <div class="p-4 bg-rose-900/40 border border-rose-500 rounded-lg text-rose-200 font-mono text-sm">
                        🚨 Security Violation: Active Organization Context (X-Tenant-ID) Missing.
                    </div>
                    """,
                    status_code=401,
                )

            return JSONResponse(
                status_code=401,
                content={
                    "error": "Unauthorized",
                    "message": "Missing required X-Tenant-ID header for tenant-scoped endpoint.",
                },
            )

        # 3. Lock the tenant context for this specific async execution pipeline
        token = set_tenant_id(tenant_id)

        try:
            # 4. Execute downstream route handlers and ORM queries
            response = await call_next(request)
            return response
        finally:
            # 5. IMMUTABLE GUARANTEE: Reset context even if the route threw a 500 crash
            reset_tenant_id(token)
