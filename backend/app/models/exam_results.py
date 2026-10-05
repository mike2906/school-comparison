from datetime import datetime
from typing import TYPE_CHECKING, Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.school import School


class ExamResult(Base):
    __tablename__ = "exam_results"
    __table_args__ = (
        UniqueConstraint(
            "school_id",
            "year",
            "exam_type",
            "subject",
            "metric",
            name="uq_exam_results_school_year_exam_subject_metric",
        ),
        Index(
            "ix_exam_results_exam_type_year_school_id",
            "exam_type",
            "year",
            "school_id",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("schools.id"), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    exam_type: Mapped[str] = mapped_column(String(50), nullable=False)
    subject: Mapped[str] = mapped_column(String(100), nullable=False)
    metric: Mapped[str] = mapped_column(String(100), nullable=False)
    value: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False)
    source_url: Mapped[Optional[str]] = mapped_column(String(1000))
    scraped_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    school: Mapped["School"] = relationship("School", back_populates="exam_results")
