import hashlib
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.scrape_log import ErrorType, ScrapeLog, ScrapeStatus, ScrapeType


class BaseScraper(ABC):
    """Base class for all scrapers with logging and change detection."""

    def __init__(self, db: AsyncSession, scrape_type: ScrapeType):
        self.db = db
        self.scrape_type = scrape_type

    @staticmethod
    def compute_hash(content: str) -> str:
        """Compute SHA-256 hash of content for change detection."""
        return hashlib.sha256(content.encode()).hexdigest()

    async def log_scrape(
        self,
        school_id: Optional[int],
        status: ScrapeStatus,
        source_url: Optional[str] = None,
        page_hash: Optional[str] = None,
        raw_html: Optional[str] = None,
        error_message: Optional[str] = None,
        error_type: Optional[ErrorType] = None,
        model_used: Optional[str] = None,
    ) -> ScrapeLog:
        """Log a scrape attempt to the database."""
        log = ScrapeLog(
            school_id=school_id,
            scrape_type=self.scrape_type,
            status=status,
            source_url=source_url,
            page_hash=page_hash,
            raw_html=raw_html,
            error_message=error_message,
            error_type=error_type,
            model_used=model_used,
            scraped_at=datetime.utcnow(),
        )
        self.db.add(log)
        await self.db.commit()
        return log

    @abstractmethod
    async def scrape(self, *args, **kwargs):
        """Execute the scrape. Must be implemented by subclasses."""
        pass
