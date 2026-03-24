from functools import lru_cache
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools"
    database_echo: bool = False

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # OpenRouter (for AI features)
    openrouter_api_key: str = ""

    # Model tier overrides (Phase 1 scraping pipeline)
    # Set this to override default model selection.
    model_tier_cheap: str = ""  # Default: google/gemini-2.5-flash-lite
    model_tier_capable: str = ""  # Default: openai/gpt-4o-mini

    # Spot-check validation settings
    spot_check_sample_size: int = 10  # Number of schools to spot-check per run (-1 = all, for calibration)
    spot_check_discrepancy_threshold: float = 0.15  # Advisory monitoring alert threshold (non-gating)
    validation_batch_concurrency: int = 3
    validation_spot_check_timeout_seconds: float = 20.0
    validation_spot_check_max_content_chars: int = 12000
    validation_spot_check_max_extracted_chars: int = 8000

    # Pipeline alerting
    alert_webhook_url: str = ""  # Slack/Discord webhook URL for pipeline alerts
    alert_failure_threshold: float = 0.10  # Alert if >10% of schools fail

    # App settings
    debug: bool = True
    secret_key: str = "change-this-in-production"
    allowed_origins: str = "http://localhost:5173"

    # Scraping settings
    scrape_delay_seconds: int = 2
    website_search_providers: str = "searxng,brave"
    searxng_base_url: str = "http://localhost:8080"
    website_search_timeout_seconds: float = 6.0
    website_search_provider_disable_seconds: int = 300
    url_validation_http_timeout_seconds: float = 4.0
    url_validation_retry_http_timeout_seconds: float = 8.0
    url_validation_timeout_terminal_threshold: int = 3
    url_validation_concurrency: int = 4
    url_validation_max_concurrency: int = 8
    url_validation_llm_timeout_seconds: float = 25.0
    url_recovery_candidate_attempts: int = 6
    url_recovery_concurrency: int = 3

    # Extraction settings
    extraction_llm_timeout_seconds: float = 15.0
    extraction_max_content_chars: int = 15000
    extraction_general_info_min_quality_score: int = 4
    extraction_output_retries: int = 2
    extraction_temperature: float = 0.0
    # Optional OpenRouter model routing controls.
    # Comma-separated list of fallback model ids, e.g. "openai/gpt-4o-mini,anthropic/claude-3.5-haiku".
    extraction_openrouter_models: str = ""
    # Comma-separated provider priority, e.g. "openai,anthropic,google-ai-studio".
    extraction_openrouter_provider_order: str = ""
    # When false, disable OpenRouter's provider-level fallback behavior.
    extraction_openrouter_provider_allow_fallbacks: bool = True
    # Optional provider sort hint: price|throughput|latency.
    extraction_openrouter_provider_sort: str = ""
    extraction_batch_concurrency: int = 1
    # When true, clear existing scraped pricing rows if extraction finds no pricing info.
    extraction_clear_pricing_on_no_info: bool = False
    nav_batch_concurrency: int = 3
    # Per-school hard timeout for CLI batch navigation. Prevents a single hung browser
    # from stalling the entire batch. Set to 0 to disable (not recommended with crawl4ai).
    nav_school_timeout_seconds: float = 120.0
    # Per-school hard timeout for CLI batch extraction (covers all LLM calls + retries).
    extraction_school_timeout_seconds: float = 180.0
    summarization_llm_timeout_seconds: float = 20.0
    summarization_batch_concurrency: int = 2

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
        "extra": "ignore",
    }

    @property
    def allowed_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.allowed_origins.split(",")]

    @field_validator("debug", mode="before")
    @classmethod
    def _normalize_debug(cls, value):
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"release", "prod", "production"}:
                return False
            if lowered in {"debug", "dev", "development"}:
                return True
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
