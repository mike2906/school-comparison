"""Fee discounts at the publish boundary: which stored discounts may be shown.

The fees-agent stage stores ``attributes["fee_discounts"]``; only discounts that passed its
page checks are stored. This drops anything malformed or from another schema version, so the
API never serialises the stored block itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Optional

from app.utils.school_facts import _site

SCHEMA_VERSION = 1
ATTRIBUTE_KEY = "fee_discounts"
KINDS = frozenset({"sibling", "early_payment", "pay_in_full", "other"})


def _number(value: Any) -> Optional[float]:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def publishable_fee_discounts(attributes: Any, website_url: Optional[str]) -> Optional[list[dict[str, Any]]]:
    """``[{"kind", "percent", "amount", "currency", "child", "text_i18n", "source_url"}]``, or None.

    Shown only while ``website_url`` is still the site they were read from."""
    block = attributes.get(ATTRIBUTE_KEY) if isinstance(attributes, Mapping) else None
    if not isinstance(block, Mapping) or block.get("_schema_version") != SCHEMA_VERSION:
        return None
    if not _site(website_url) or _site(block.get("website_url")) != _site(website_url):
        return None
    discounts = []
    for item in block.get("discounts") or []:
        if not isinstance(item, Mapping):
            continue
        text = item.get("text_i18n")
        url = item.get("source_url")
        if (
            item.get("kind") in KINDS
            and isinstance(url, str)
            and url.startswith(("http://", "https://"))
            and isinstance(text, Mapping)
            and all(isinstance(text.get(lang), str) and text[lang].strip() for lang in ("bg", "en"))
        ):
            currency = item.get("currency")
            child = item.get("child")
            discounts.append(
                {
                    "kind": item["kind"],
                    "percent": _number(item.get("percent")),
                    "amount": _number(item.get("amount")),
                    "currency": currency if isinstance(currency, str) else None,
                    "child": child if isinstance(child, int) and not isinstance(child, bool) else None,
                    "text_i18n": {"bg": text["bg"], "en": text["en"]},
                    "source_url": url,
                }
            )
    return discounts or None
