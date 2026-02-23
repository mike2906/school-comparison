"""Tests for extraction rule module resolution."""

from types import SimpleNamespace

from app.scrapers import extraction_rules


def test_get_rules_returns_country_module():
    extraction_rules.get_rules.cache_clear()
    module = extraction_rules.get_rules("bg")
    assert module.__name__ == "app.scrapers.extraction_rules.bg"


def test_get_rules_falls_back_to_base_for_unknown_country():
    extraction_rules.get_rules.cache_clear()
    module = extraction_rules.get_rules("xx")
    assert module.__name__ == "app.scrapers.extraction_rules.base"


def test_get_rules_uses_default_country_when_not_provided(monkeypatch):
    extraction_rules.get_rules.cache_clear()
    monkeypatch.setattr(
        extraction_rules,
        "get_settings",
        lambda: SimpleNamespace(default_country="bg"),
    )
    module = extraction_rules.get_rules("")
    assert module.__name__ == "app.scrapers.extraction_rules.bg"


def test_get_rules_defaults_to_bg_when_default_country_missing(monkeypatch):
    extraction_rules.get_rules.cache_clear()
    monkeypatch.setattr(extraction_rules, "get_settings", lambda: SimpleNamespace())
    module = extraction_rules.get_rules(None)
    assert module.__name__ == "app.scrapers.extraction_rules.bg"

