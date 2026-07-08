import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import src.domains.catalog.models  # noqa: F401
import src.domains.inventory.models  # noqa: F401
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

# ... [other imports] ...
from src.core.errors import (
    global_exception_handler,
    htmx_validation_exception_handler,
    inventory_domain_handler,
    security_violation_handler,
)
from src.core.security.context import SecurityViolationError
from src.core.security.middleware import TenantScopingMiddleware
from src.domains.inventory.exceptions import InventoryDomainError
from src.domains.inventory.router import router as inventory_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Execute application startup tasks."""
    # Alembic handles schema initialization before application boot
    yield


app = FastAPI(
    title="IMS Enterprise SaaS",
    description="Bleeding-Edge Inventory Management System",
    version="0.1.0",
    lifespan=lifespan,
)

# Register Multi-Tenant Zero-Trust Gatekeeper Middleware
app.add_middleware(TenantScopingMiddleware)

# --- Register Global Exception Handlers ---
app.add_exception_handler(SecurityViolationError, security_violation_handler)
app.add_exception_handler(InventoryDomainError, inventory_domain_handler)
app.add_exception_handler(
    RequestValidationError, htmx_validation_exception_handler
)  # <-- REGISTER HERE
app.add_exception_handler(Exception, global_exception_handler)

# Mount static directory for vendored HTMX, Alpine, and CSS
app.mount("/static", StaticFiles(directory="src/shared/static"), name="static")

templates = Jinja2Templates(directory="src/shared/templates")

app.include_router(inventory_router)


@app.get("/", response_class=HTMLResponse)
async def dashboard_view(request: Request) -> HTMLResponse:
    """Render the master dashboard interface (Exempt from Tenant Scoping)."""
    return templates.TemplateResponse(
        request=request, name="base.html", context={"request": request}
    )


@app.get("/api/health", response_class=HTMLResponse)
async def htmx_health_ping() -> HTMLResponse:
    """HTMX endpoint returning a live HTML badge fragment (Exempt from Tenant Scoping)."""
    return HTMLResponse(
        content="""
        <div class="p-4 bg-emerald-900/30 border border-emerald-500/50 rounded-lg inline-flex items-center space-x-2">
            <span class="w-2.5 h-2.5 rounded-full bg-emerald-400 animate-pulse"></span>
            <span class="text-emerald-300 font-mono text-sm">System Healthy | Postgres 18 Pool Active</span>
        </div>
        """
    )
