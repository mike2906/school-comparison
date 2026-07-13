from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.pricing import PriceCategory, PricePeriod, PriceSource


class PricingContextResponse(BaseModel):
    """Explicit public projection of the internal pricing context JSON."""

    notes: Optional[str] = None
    confidence: Optional[float] = None
    discounts: list[str] = Field(default_factory=list)
    installments: list[str] = Field(default_factory=list)
    includes: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)

    # Unknown internal keys are deliberately discarded at serialization.
    model_config = ConfigDict(extra="ignore")


class PricingBase(BaseModel):
    age_group: Optional[str] = None
    category: PriceCategory
    academic_year: Optional[str] = None
    amount: Optional[float] = None
    amount_min: Optional[float] = None
    amount_max: Optional[float] = None
    currency: str = "BGN"
    period: PricePeriod
    plan_name: Optional[str] = None
    pricing_context: Optional[PricingContextResponse] = None
    source: PriceSource
    source_url: Optional[str] = None


class PricingResponse(PricingBase):
    id: int
    school_id: int
    scraped_at: datetime

    model_config = {"from_attributes": True}
