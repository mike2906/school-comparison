"""Country-specific extraction rule sets for Stage 5 extraction."""

from __future__ import annotations

from functools import lru_cache
from importlib import import_module
from types import ModuleType

from app.config import get_settings


@lru_cache(maxsize=32)
def get_rules(country_code: str | None) -> ModuleType:
    """Return extraction rules module for a country, with safe fallback."""
    normalized = (country_code or "").strip().lower()
    if not normalized:
        normalized = str(getattr(get_settings(), "default_country", "bg")).strip().lower() or "bg"

    module_path = f"app.scrapers.extraction_rules.{normalized}"
    try:
        return import_module(module_path)
    except ModuleNotFoundError as exc:
        # Only fallback when the country module itself is missing.
        # Bubble up nested import errors to avoid hiding real bugs.
        if exc.name != module_path:
            raise
        return import_module("app.scrapers.extraction_rules.base")


__all__ = ["get_rules"]
