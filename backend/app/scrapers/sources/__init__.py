"""
Source adapter registry.

This module provides a registry of all discovery adapters.
New country adapters are registered here.
"""
from typing import Optional, Type

from app.scrapers.sources.base_adapter import BaseSourceAdapter

# Adapter registry: {adapter_name: adapter_class}
_ADAPTER_REGISTRY: dict[str, Type[BaseSourceAdapter]] = {}


def register_adapter(adapter_class: Type[BaseSourceAdapter]) -> Type[BaseSourceAdapter]:
    """
    Register a source adapter.

    Usage:
        @register_adapter
        class KgSofiaBgAdapter(BaseSourceAdapter):
            ...
    """
    _ADAPTER_REGISTRY[adapter_class.ADAPTER_NAME] = adapter_class
    return adapter_class


def get_adapter(name: str) -> Type[BaseSourceAdapter]:
    """
    Get an adapter class by name.

    Args:
        name: Adapter name (e.g., "kg_sofia_bg")

    Returns:
        Adapter class

    Raises:
        KeyError: If adapter not found
    """
    if name not in _ADAPTER_REGISTRY:
        available = ", ".join(_ADAPTER_REGISTRY.keys())
        raise KeyError(f"Adapter '{name}' not found. Available adapters: {available}")
    return _ADAPTER_REGISTRY[name]


def get_adapters_for_country(country_code: str, city: Optional[str] = None) -> list[Type[BaseSourceAdapter]]:
    """
    Get all adapters for a given country/city.

    Args:
        country_code: Country code (e.g., "bg")
        city: Optional city filter (e.g., "sofia")

    Returns:
        List of adapter classes matching the criteria
    """
    adapters = []
    for adapter_class in _ADAPTER_REGISTRY.values():
        if adapter_class.COUNTRY_CODE == country_code:
            if city is None or adapter_class.CITY == city or adapter_class.CITY is None:
                adapters.append(adapter_class)
    return adapters


def list_adapters() -> dict[str, dict[str, str]]:
    """
    List all registered adapters with metadata.

    Returns:
        Dict of {adapter_name: {country_code, city, description}}
    """
    return {
        name: {
            "country_code": adapter.COUNTRY_CODE,
            "city": adapter.CITY or "country-wide",
            "description": adapter.DESCRIPTION,
        }
        for name, adapter in _ADAPTER_REGISTRY.items()
    }


# Import adapters to trigger registration
# The @register_adapter decorator automatically adds them to the registry
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter  # noqa: F401
from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter  # noqa: F401

# Future adapters:
# from app.scrapers.sources.bg.web_search import WebSearchAdapter
