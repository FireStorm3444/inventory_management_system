import uuid

from pydantic import BaseModel, Field, model_validator


class CartItemSchema(BaseModel):
    """Represents a single line item in a checkout request."""

    product_id: uuid.UUID
    location_id: uuid.UUID
    quantity: int = Field(
        strict=True,
        gt=0,
        description="Checkout quantity must be a strict integer greater than zero.",
    )


class CartCheckoutRequest(BaseModel):
    """Payload for completing a multi-item transaction."""

    items: list[CartItemSchema] = Field(
        min_length=1, description="A checkout request must contain at least one item."
    )
    reference_id: str = Field(
        min_length=1,
        max_length=100,
        description="External reference, e.g., Stripe Payment Intent ID or Order ID.",
    )
    from_reserved: bool = Field(
        default=False,
        description="Set to true if finalizing a previously held soft-reservation cart.",
    )

    @model_validator(mode="after")
    def validate_unique_lines(self) -> CartCheckoutRequest:
        """Edge Case Prevention: Reject payloads with duplicate product/location pairs.

        Clients must aggregate quantities for the same SKU before sending the request.
        """
        seen = set()
        for item in self.items:
            key = (str(item.location_id), str(item.product_id))
            if key in seen:
                raise ValueError(
                    f"Duplicate line item detected for product '{item.product_id}' "
                    f"at location '{item.location_id}'. Please aggregate quantities."
                )
            seen.add(key)
        return self


class StockAdjustmentRequest(BaseModel):
    """Payload for manual inventory corrections, receipts, or damage write-offs."""

    product_id: uuid.UUID
    location_id: uuid.UUID
    physical_delta: int = Field(
        strict=True, description="Positive for receipts, negative for deductions."
    )
    reason: str = Field(
        min_length=3,
        max_length=50,
        description="Audit code (e.g., 'PURCHASE_RECEIPT', 'DAMAGE', 'SHRINKAGE').",
    )
    reference_id: str | None = Field(
        default=None, max_length=100, description="Optional tracking reference (e.g., PO Number)."
    )

    @model_validator(mode="after")
    def validate_meaningful_delta(self) -> StockAdjustmentRequest:
        """Edge Case Prevention: Reject zero-value adjustments."""
        if self.physical_delta == 0:
            raise ValueError("Stock adjustment physical_delta cannot be exactly zero.")
        return self
