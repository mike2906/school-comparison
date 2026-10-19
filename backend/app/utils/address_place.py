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
# Столична municipality's settlements outside the city by EKATTE code, which is what the
# MoE register's ``moe_town_code`` is. Sofia-city rows were imported before the importer
# stored ``moe_town_name``, so the code is often all a school has (НСИ ЕКАТТЕ, SOF46).
STOLICHNA_SETTLEMENTS = {
    357: "Нови Искър",
    2511: "Балша",
    2659: "Банкя",
    4234: "Бистрица",
    7106: "Бусманци",
    7140: "Бухово",
    11394: "Владая",
    11884: "Войнеговци",
    12084: "Волуяк",
    14831: "Герман",
    16448: "Горни Богров",
    21662: "Доброславци",
    22304: "Долни Богров",
    22472: "Долни Пасарел",
    29150: "Железница",
    29204: "Желява",
    29430: "Житен",
    32216: "Иваняне",
    35239: "Казичене",
    37280: "Клисура",
    37914: "Кокаляне",
    39791: "Кривина",
    40436: "Кубратово",
    41010: "Кътина",
    44063: "Лозен",
    44224: "Локорско",
    46721: "Мало Бучино",
    48393: "Мировяне",
    49206: "Мрамор",
    49597: "Мърчаево",
    51250: "Негован",
    55419: "Панчарево",
    56624: "Плана",
    57011: "Подгумер",
    65601: "Световрачене",
    80409: "Чепинци",
    87401: "Яна",
}
# София-град and София-област: the settlements whose names we show beside an address.
SOFIA_REGION_CODES = frozenset({22, 23})

# The capital's districts, as kg.sofia and the cut-off import name them. A location's
# ``district`` column can hold another place's neighbourhood; only these are shown.
SOFIA_DISTRICTS = frozenset({
    "Банкя", "Витоша", "Връбница", "Възраждане", "Изгрев", "Илинден", "Искър",
    "Красна поляна", "Красно село", "Кремиковци", "Лозенец", "Люлин", "Младост",
    "Надежда", "Нови Искър", "Оборище", "Овча купел", "Панчарево", "Подуяне", "Сердика",
    "Слатина", "Средец", "Студентски", "Триадица",
})

# An address that names a settlement or district itself ("с. Лозен", "гр. Банкя",
# "район Витоша", "р-н Панчарево") already says where it is. A settlement opens the
# address or follows a comma: "ул. „С. Врачански“" is a street.
_SETTLEMENT_RE = re.compile(r"(?:^|,)\s*(?:[сС]\.|[гГ][рР]\.|[сС]ело\s)\s*[А-Я]")
_DISTRICT_RE = re.compile(r"(?<![\w-])(?:район|р-н)(?![\w-])", flags=re.IGNORECASE)
# "ул. Сливница 3", "бул. „Витоша“": the name is a street's, not the place's.
_STREET_BEFORE_RE = re.compile(r"(?:ул|бул|пл|площад|улица|булевард)\.?\s*[„\"]?\s*$", flags=re.IGNORECASE)


def school_town(attributes: Mapping[str, Any] | None) -> str | None:
    """The settlement outside the city of Sofia a school is in, from the MoE register."""
    attrs = attributes if isinstance(attributes, Mapping) else {}
    region = attrs.get("moe_region_code")
    try:
        region = int(region)
    except (TypeError, ValueError):
        return None
    town = attrs.get("moe_town_name")
    if not isinstance(town, str) and region == 22:
        try:
            town = STOLICHNA_SETTLEMENTS.get(int(attrs.get("moe_town_code")))
        except (TypeError, ValueError):
            town = None
    if region not in SOFIA_REGION_CODES or not isinstance(town, str):
        return None
    town = " ".join(town.split())
    return town if town and town.casefold() != SOFIA.casefold() else None


def _names(address: str, name: str) -> bool:
    """Whether the address names this place itself (not a street called after it)."""
    for match in re.finditer(rf"(?<![\w]){re.escape(name)}(?![а-я])", address, flags=re.IGNORECASE):
        if not _STREET_BEFORE_RE.search(address[: match.start()]):
            return True
    return False


def place_suffix_bg(address_bg: str | None, *, town: str | None, district: str | None) -> str | None:
    """", Горни Богров, район Кремиковци" parts the Bulgarian address leaves out, or None."""
    address = address_bg or ""
    parts: list[str] = []
    town = " ".join(town.split()) if isinstance(town, str) else ""
    if town and not _SETTLEMENT_RE.search(address) and not _names(address, town):
        parts.append(town)
    district = " ".join(district.split()) if isinstance(district, str) else ""
    if (
        district in SOFIA_DISTRICTS
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
        # A stored English address may already name a part the Bulgarian one leaves out.
        parts = [english_address(part) or part for part in suffix.split(", ")]
        missing = [
            part
            for part in parts
            if not re.search(rf"\b{re.escape(part.split(' district')[0])}\b", out["en"], flags=re.IGNORECASE)
        ]
        if missing:
            out["en"] = f"{out['en']}, {', '.join(missing)}"
    return out
