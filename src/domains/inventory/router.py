import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession
from src.core.database import get_db
from src.core.idempotency import get_idempotency_key
from src.domains.inventory.schemas import CartCheckoutRequest, StockAdjustmentRequest
from src.domains.inventory.service import CartItemDTO, adjust_stock, checkout_cart

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/inventory", tags=["Inventory"])


def is_htmx_request(request: Request) -> bool:
    """Helper to detect if the request was initiated by the HTMX frontend."""
    return request.headers.get("HX-Request") == "true"


@router.post("/adjust")
async def api_adjust_stock(
    request: Request,
    payload: StockAdjustmentRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Idempotent endpoint to manually adjust stock levels (e.g., receipts or damage)."""

    # The transaction engine takes over. If 'idempotency_key' was seen before,
    # it throws an IdempotencyConflictError (caught by our Phase 3 global interceptor).
    balance = await adjust_stock(
        session=db,
        product_id=payload.product_id,
        location_id=payload.location_id,
        physical_delta=payload.physical_delta,
        reason=payload.reason,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
    )

    # Content Negotiation: Adapt response for the client type
    if is_htmx_request(request):
        # Return a precise, compiled HTML fragment that HTMX swaps directly into the DOM
        html = f"""
        <span id="stock-badge-{balance.product_id}"
              class="px-2 py-1 bg-green-100 text-green-800 rounded font-mono text-sm shadow-sm transition-all">
            {balance.available_qty} Available
        </span>
        """
        return HTMLResponse(content=html)

    # Standard B2B API Response
    return {"status": "success", "available_qty": balance.available_qty}


@router.post("/checkout")
async def api_checkout_cart(
    request: Request,
    payload: CartCheckoutRequest,
    db: AsyncSession = Depends(get_db),
    idempotency_key: str = Depends(get_idempotency_key),
):
    """Idempotent endpoint to securely checkout a multi-item cart."""

    # FIX: Map inbound network schema lines to pure domain DTOs
    domain_items = [
        CartItemDTO(
            product_id=item.product_id,
            location_id=item.location_id,
            quantity=item.quantity,
        )
        for item in payload.items
    ]

    # Pass the correctly typed domain_items into the core stock engine
    updated_balances = await checkout_cart(
        session=db,
        items=domain_items,
        reference_id=payload.reference_id,
        idempotency_key=idempotency_key,
        from_reserved=payload.from_reserved,
    )

    if is_htmx_request(request):
        html = """
        <div hx-swap-oob="true" id="cart-status">
            <span class="text-green-600 font-bold tracking-tight">Checkout Completed</span>
        </div>
        """
        return HTMLResponse(
            content=html,
            headers={
                "HX-Trigger": '{"show-toast": {"level": "success", "message": "Transaction cleared successfully."}}'
            },
        )

    return {
        "status": "success",
        "processed_items": len(updated_balances),
        "reference_id": payload.reference_id,
    }
