from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.pricing import PriceCategory, PricePeriod, PriceSource
from app.utils.academic_year import academic_year_status, normalize_academic_year


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
    period: Optional[PricePeriod] = None
    plan_name: Optional[str] = None
    pricing_context: Optional[PricingContextResponse] = None
    source: PriceSource
    source_url: Optional[str] = None


class PricingResponse(PricingBase):
    id: int
    school_id: int
    scraped_at: datetime

    @computed_field(return_type=Optional[str])
    @property
    def academic_year_canonical(self) -> Optional[str]:
        """The year in ``YYYY/YYYY`` form; ``None`` when the school stated none."""
        return normalize_academic_year(self.academic_year)

    @computed_field(return_type=str)
    @property
    def year_status(self) -> str:
        """``current``, ``dated_other`` or ``not_stated`` for this row.

        Derived per request rather than stored, because which year counts as current
        changes every September.
        """
        return academic_year_status(self.academic_year)

    model_config = {"from_attributes": True}
