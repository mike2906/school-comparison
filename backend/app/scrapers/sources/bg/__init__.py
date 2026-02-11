"""
Bulgaria-specific source adapters.

Adapters in this package discover schools from Bulgarian government registries
and other Bulgaria-specific sources.
"""
from app.scrapers.sources.bg.kg_sofia import KgSofiaBgAdapter
from app.scrapers.sources.bg.moe_registry import MoeRegistryAdapter

# Future adapters:
# from app.scrapers.sources.bg.web_search import WebSearchAdapter
# from app.scrapers.sources.bg.nvo_platform import NvoPlatformAdapter

__all__ = ["KgSofiaBgAdapter", "MoeRegistryAdapter"]
