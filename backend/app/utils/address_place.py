"""Where an address is: the village and Sofia district a register address leaves out.

The MoE register gives a village school's street alone ("ул. „М. Райчев“ 9" for 115 ОУ in
Горни Богров), and many Sofia addresses name only a numbered street or a micro-district
("ул. 501 V м. р."). A parent cannot place either. The display address gets the school's
settlement (``attributes.moe_town_name``) and its district, each only when the address
does not already name it. Stored addresses are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.utils.display_names import english_address

SOFIA = "София"
# София-град and София-област: the settlements whose names we show beside an address.
SOFIA_REGION_CODES = frozenset({22, 23})

# An address that names a settlement or district itself ("с. Лозен", "гр. Банкя",
# "район Витоша", "р-н Панчарево") already says where it is.
_SETTLEMENT_RE = re.compile(r"(?<![\w-])(?:[сС]\.|[гГ][рР]\.|[сС]ело\s)\s*[А-Я]")
_DISTRICT_RE = re.compile(r"(?<![\w-])(?:район|р-н)(?![\w-])", flags=re.IGNORECASE)


def school_town(attributes: Mapping[str, Any] | None) -> str | None:
    """The settlement outside the city of Sofia a school is in, from the MoE register."""
    attrs = attributes if isinstance(attributes, Mapping) else {}
    region = attrs.get("moe_region_code")
    try:
        region = int(region)
    except (TypeError, ValueError):
        return None
    town = attrs.get("moe_town_name")
    if region not in SOFIA_REGION_CODES or not isinstance(town, str):
        return None
    town = " ".join(town.split())
    return town if town and town.casefold() != SOFIA.casefold() else None


def _names(address: str, name: str) -> bool:
    return re.search(rf"(?<![\w]){re.escape(name)}(?![а-я])", address, flags=re.IGNORECASE) is not None


def place_suffix_bg(address_bg: str | None, *, town: str | None, district: str | None) -> str | None:
    """", Горни Богров, район Кремиковци" parts the Bulgarian address leaves out, or None."""
    address = address_bg or ""
    parts: list[str] = []
    town = " ".join(town.split()) if isinstance(town, str) else ""
    if town and not _SETTLEMENT_RE.search(address) and not _names(address, town):
        parts.append(town)
    district = " ".join(district.split()) if isinstance(district, str) else ""
    if (
        district
        and district.casefold() != town.casefold()
        and not _DISTRICT_RE.search(address)
        and not _names(address, district)
    ):
        parts.append(f"район {district}")
    return ", ".join(parts) or None


def with_place(
    resolved: Mapping[str, str], *, town: str | None, district: str | None
) -> dict[str, str]:
    """The resolved display address with the missing settlement and district appended."""
    out = dict(resolved)
    suffix = place_suffix_bg(resolved.get("bg"), town=town, district=district)
    if not suffix:
        return out
    if out.get("bg"):
        out["bg"] = f"{out['bg']}, {suffix}"
    if out.get("en"):
        out["en"] = f"{out['en']}, {english_address(suffix) or suffix}"
    return out
