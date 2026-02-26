import enum
from datetime import datetime
from typing import Optional

from sqlalchemy import String, Text, Enum, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class ScrapeType(str, enum.Enum):
    DISCOVERY = "discovery"
    REGISTRY = "registry"
    WEBSITE = "website"
    PRICES = "prices"
    NVO = "nvo"
    SOCIAL = "social"


class ScrapeStatus(str, enum.Enum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ErrorType(str, enum.Enum):
    TRANSIENT = "transient"
    PERMANENT = "permanent"
    BLOCKED = "blocked"


class ScrapeLog(Base):
    __tablename__ = "scrape_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[Optional[int]] = mapped_column(Integer)
    scrape_type: Mapped[ScrapeType] = mapped_column(Enum(ScrapeType), nullable=False)
    status: Mapped[ScrapeStatus] = mapped_column(Enum(ScrapeStatus), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    page_hash: Mapped[Optional[str]] = mapped_column(String(64))
    raw_html: Mapped[Optional[str]] = mapped_column(Text)
    error_message: Mapped[Optional[str]] = mapped_column(Text)
    error_type: Mapped[Optional[ErrorType]] = mapped_column(Enum(ErrorType))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    next_retry_at: Mapped[Optional[datetime]] = mapped_column(DateTime)
    model_used: Mapped[Optional[str]] = mapped_column(String(100))

    # Pipeline tracking fields (Phase 1)
    run_id: Mapped[Optional[str]] = mapped_column(String(36))  # Links to pipeline_runs.id
    llm_input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    llm_output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)

    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
