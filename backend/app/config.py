from pydantic_settings import BaseSettings
from functools import lru_cache


class Settings(BaseSettings):
    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/sofia_schools"

    # Redis
    REDIS_URL: str = "redis://localhost:6379/0"

    # OpenRouter (for AI features)
    openrouter_api_key: str = ""

    # App settings
    debug: bool = True
    secret_key: str = "change-this-in-production"
    allowed_origins: str = "http://localhost:5173"

    # Scraping settings
    scrape_delay_seconds: int = 2

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
