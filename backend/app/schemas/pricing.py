from datetime import datetime
from typing import Optional

from pydantic import BaseModel

from app.models.pricing import PriceCategory, PricePeriod, PriceSource


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
    pricing_context: Optional[dict] = None
    source: PriceSource
    source_url: Optional[str] = None


class PricingResponse(PricingBase):
    id: int
    school_id: int
    scraped_at: datetime

    model_config = {"from_attributes": True}
