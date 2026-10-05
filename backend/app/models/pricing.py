import enum
from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.school import School
    from app.models.source_page import SourcePage


class PriceCategory(str, enum.Enum):
    TUITION = "tuition"
    FOOD = "food"
    TRANSPORT = "transport"
    ACTIVITIES = "activities"
    REGISTRATION = "registration"
    MATERIALS = "materials"
    EXTENDED_DAY = "extended_day"
    UNIFORMS = "uniforms"
    EXTRACURRICULAR = "extracurricular"
    CAMP = "camp"


class PricePeriod(str, enum.Enum):
    MONTHLY = "monthly"
    YEARLY = "yearly"
    ONE_TIME = "one_time"
    QUARTER = "quarter"
    TERM = "term"
    SEMESTER = "semester"


class PriceSource(str, enum.Enum):
    OFFICIAL = "official"
    SCRAPED_WEBSITE = "scraped_website"
    FORUM = "forum"
    NOT_FOUND = "not_found"


class Pricing(Base):
    __tablename__ = "pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    age_group: Mapped[Optional[str]] = mapped_column(String(50))
    category: Mapped[PriceCategory] = mapped_column(Enum(PriceCategory), nullable=False)
    academic_year: Mapped[Optional[str]] = mapped_column(String(20))
    amount: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    amount_min: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    amount_max: Mapped[Optional[float]] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3), default="BGN")
    # NULL means the school did not state how often the fee is charged. Never fill it
    # with a guess just to have a value.
    period: Mapped[Optional[PricePeriod]] = mapped_column(Enum(PricePeriod), nullable=True)
    plan_name: Mapped[Optional[str]] = mapped_column(String(100))
    pricing_context: Mapped[Optional[dict]] = mapped_column(JSON)
    source: Mapped[PriceSource] = mapped_column(Enum(PriceSource), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    # Evidence link: a price is publishable only while the page it came from is still
    # valid. Production navigation upserts source pages in place, so this survives a
    # routine crawl; SET NULL is only a backstop for a genuinely deleted page.
    source_page_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("source_pages.id", ondelete="SET NULL"), nullable=True
    )
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    school: Mapped["School"] = relationship("School", back_populates="pricing")
    # ``raise_on_sql`` keeps a forgotten eager-load loud instead of silently emitting
    # a lazy query (which async would fail on anyway) or gating on a stale None.
    source_page: Mapped[Optional["SourcePage"]] = relationship(
        "SourcePage", lazy="raise_on_sql"
    )
