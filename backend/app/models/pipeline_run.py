import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import String, Integer, DateTime, Enum, Float, Text, JSON
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class PipelineStatus(str, enum.Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PARTIAL = "partial"  # Some schools succeeded, some failed


class PipelineStage(str, enum.Enum):
    DISCOVER = "discover"
    VALIDATE_URLS = "validate_urls"
    NAVIGATE = "navigate"
    EXTRACT = "extract"
    VALIDATE_DATA = "validate_data"
    SUMMARIZE = "summarize"
    FULL = "full"  # Full pipeline run


class PipelineRun(Base):
    """Tracks end-to-end pipeline executions."""

    __tablename__ = "pipeline_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)  # UUID
    country_code: Mapped[str] = mapped_column(String(2), nullable=False)
    city: Mapped[Optional[str]] = mapped_column(String(100))  # e.g., "sofia"
    stage: Mapped[PipelineStage] = mapped_column(Enum(PipelineStage), nullable=False)
    status: Mapped[PipelineStatus] = mapped_column(Enum(PipelineStatus), nullable=False, default=PipelineStatus.RUNNING)

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    schools_processed: Mapped[int] = mapped_column(Integer, default=0)
    schools_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    schools_failed: Mapped[int] = mapped_column(Integer, default=0)
    schools_skipped: Mapped[int] = mapped_column(Integer, default=0)  # Content unchanged, skipped extraction

    total_llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)  # Aggregated error messages
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # Snapshot of pipeline config

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
