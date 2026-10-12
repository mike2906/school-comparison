#!/usr/bin/env python3
"""List programme, facility and activity items the site shows untranslated.

    uv run python scripts/list_untranslated_items.py [API_URL]

Reads the public ``/schools`` list (default: production) and prints, as JSON, the items
each locale shows in the other language with no entry in
``app/utils/list_translations.json``: ``{"bg_to_en": [...], "en_to_bg": [...]}``.
Translate them and add the pairs to that file. Read-only.
"""
from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.utils.list_translations import is_untranslated  # noqa: E402

FIELDS = ("special_programs", "facilities", "activities_offered")
DIRECTIONS = {"en": "bg_to_en", "bg": "en_to_bg"}


def main() -> None:
    api_url = sys.argv[1] if len(sys.argv) > 1 else "https://api.schooldecider.com"
    with urllib.request.urlopen(f"{api_url.rstrip('/')}/schools?limit=10000") as response:
        schools = json.load(response)

    missing: dict[str, set[str]] = {direction: set() for direction in DIRECTIONS.values()}
    for school in schools:
        for locale, direction in DIRECTIONS.items():
            localized = (school.get("attributes_i18n") or {}).get(locale) or {}
            for field in FIELDS:
                for item in localized.get(field) or []:
                    if is_untranslated(item, locale):
                        missing[direction].add(item)

    print(json.dumps(
        {direction: sorted(items, key=str.lower) for direction, items in missing.items()},
        ensure_ascii=False,
        indent=2,
    ))


if __name__ == "__main__":
    main()
