from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Enum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.scrape_log import ScrapeType


class SourcePage(Base):
    __tablename__ = "source_pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[Optional[int]] = mapped_column(ForeignKey("schools.id", ondelete="SET NULL"))
    scrape_type: Mapped[ScrapeType] = mapped_column(Enum(ScrapeType, name="scrapetype", create_type=False), nullable=False)
    source_url: Mapped[str] = mapped_column(String(1000), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    last_scraped_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    scrape_count: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc)
    )

    school: Mapped[Optional["School"]] = relationship("School", back_populates="source_pages")


# Import for relationship type hints
from app.models.school import School
