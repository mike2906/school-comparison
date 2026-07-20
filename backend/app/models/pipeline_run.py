import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
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
    status: Mapped[PipelineStatus] = mapped_column(
        Enum(PipelineStatus), nullable=False, default=PipelineStatus.RUNNING
    )

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    last_completed_stage: Mapped[Optional[str]] = mapped_column(String(50))
    terminalization_reason: Mapped[Optional[str]] = mapped_column(Text)

    schools_processed: Mapped[int] = mapped_column(Integer, default=0)
    schools_succeeded: Mapped[int] = mapped_column(Integer, default=0)
    schools_failed: Mapped[int] = mapped_column(Integer, default=0)
    schools_skipped: Mapped[int] = mapped_column(Integer, default=0)  # Content unchanged, skipped extraction

    total_llm_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error_summary: Mapped[Optional[str]] = mapped_column(Text)  # Aggregated error messages
    config: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # Snapshot of pipeline config
    metrics: Mapped[Optional[dict]] = mapped_column(JSON, default=dict)  # Data-quality scoreboard snapshot

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class ProviderRequestStatus(str, enum.Enum):
    PENDING = "pending"
    ATTRIBUTED = "attributed"
    UNCERTAIN = "uncertain"


class ProviderRequestLedger(Base):
    """Durable, per-request provider billing evidence for a pipeline run."""

    __tablename__ = "provider_request_ledger"
    __table_args__ = (
        UniqueConstraint(
            "client_request_id", name="uq_provider_request_ledger_client_request_id"
        ),
        UniqueConstraint(
            "provider_request_id", name="uq_provider_request_ledger_provider_request_id"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_request_id: Mapped[str] = mapped_column(String(36), nullable=False)
    provider_request_id: Mapped[Optional[str]] = mapped_column(String(255))
    pipeline_run_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipeline_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    stage: Mapped[str] = mapped_column(String(50), nullable=False)
    school_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("schools.id", ondelete="SET NULL")
    )
    model: Mapped[str] = mapped_column(String(255), nullable=False)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer)
    provider_cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(14, 8))
    status: Mapped[ProviderRequestStatus] = mapped_column(
        Enum(ProviderRequestStatus), nullable=False, default=ProviderRequestStatus.PENDING
    )
    uncertainty_reason: Mapped[Optional[str]] = mapped_column(Text)
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    attributed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )
