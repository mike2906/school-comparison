from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Country(Base):
    __tablename__ = "countries"

    code: Mapped[str] = mapped_column(String(2), primary_key=True)
    name_i18n: Mapped[dict] = mapped_column(JSON, nullable=False)
    education_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    map_config: Mapped[dict] = mapped_column(JSON, nullable=False)
    supported_languages: Mapped[list] = mapped_column(JSON, nullable=False)
    default_language: Mapped[str] = mapped_column(String(5), nullable=False)
    default_currency: Mapped[str] = mapped_column(String(3), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
