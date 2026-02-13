from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # OpenRouter (for AI features)
    openrouter_api_key: str = ""

    # Model tier overrides (Phase 1 scraping pipeline)
    # Set these to override default model selections
    model_tier_cheap: str = ""  # Default: google/gemini-2.0-flash-lite
    model_tier_medium: str = ""  # Default: google/gemini-2.5-flash
    model_tier_capable: str = ""  # Default: google/gemini-2.5-pro

    # Spot-check validation settings
    spot_check_sample_size: int = 10  # Number of schools to spot-check per run (-1 = all, for calibration)
    spot_check_discrepancy_threshold: float = 0.15  # Alert if >15% of spot-checks have discrepancies

    # Pipeline alerting
    alert_webhook_url: str = ""  # Slack/Discord webhook URL for pipeline alerts
    alert_failure_threshold: float = 0.10  # Alert if >10% of schools fail

    # App settings
    debug: bool = True
    secret_key: str = "change-this-in-production"
    allowed_origins: str = "http://localhost:5173"

    # Scraping settings
    scrape_delay_seconds: int = 2

    # Geocoding settings
    geocoding_provider: str = "composite"  # composite | nominatim | google | mapbox (composite = GeoJSON + Nominatim fallback, recommended)
    # REQUIRED: Must be set in .env (Nominatim policy requires valid contact email)
    geocoding_contact_email: str = "your-email@example.com"  # Placeholder - override in .env

    # API settings
    max_schools_to_compare: int = 5
    max_search_query_length: int = 100

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
    }

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]


@lru_cache
def get_settings() -> Settings:
    return Settings()
