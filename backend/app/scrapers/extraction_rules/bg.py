"""Bulgarian extraction rules for stage 5 website extraction.

BG currently inherits the default ruleset 1:1.
Add overrides here as country-specific behavior diverges.
"""

from __future__ import annotations

from . import base as _base

__all__ = list(_base.__all__)

for _export_name in __all__:
    globals()[_export_name] = getattr(_base, _export_name)
