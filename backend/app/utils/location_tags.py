"""Publish boundary for ``school_locations.location_tags``.

``location_tags`` is an internal scratchpad, like ``schools.attributes``: the
scrapers and repair commands stuff provenance/coords metadata into it
(``source=moe_registry``, ``source_esri_id=…``, ``coords_source=…``,
``address_source=…``, ``location_recovered=…``, ``coords_cleared=…``,
``coords_precision=…``). None of that may reach the browser.

Only the semantic *focus* tags below are public — they drive the location focus
emoji/label chips in the UI. This mirrors the frontend allowlist that used to
live in ``frontend/src/utils/locationFocus.js`` (``FOCUS_EMOJIS`` keys); keep the
two sets in sync. The projection runs at serialization (see
``SchoolLocationBase.location_tags``), so provenance strings never ship even
though they stay in the column at rest.
"""

# Canonical semantic focus tags. Keep in sync with FOCUS_EMOJIS / the
# `locationTags.*` i18n keys in the frontend.
SEMANTIC_LOCATION_TAGS: frozenset[str] = frozenset(
    {
        "science_focus",
        "arts_focus",
        "sports_focus",
        "music_focus",
        "technology_focus",
        "language_focus",
    }
)


def semantic_location_tags(tags: list[str] | None) -> list[str]:
    """Project raw ``location_tags`` down to the public semantic focus tags.

    Order is preserved and duplicates dropped, so serialized output is stable.
    """

    if not tags:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        value = str(tag)
        if value in SEMANTIC_LOCATION_TAGS and value not in seen:
            seen.add(value)
            result.append(value)
    return result
