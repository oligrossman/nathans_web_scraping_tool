"""Stable, unique dish identity.

A dish is identified by its (restaurant, item name) pair, not by the item name
alone, because the same name can appear at different restaurants (e.g.
"Mac and Cheese"). ``make_dish_id`` derives a deterministic id so the same dish
always resolves to the same id in the CSV, the SQLite mappings, and at join
time — no persisted counter required, and existing rows can be back-filled by
recomputation.

This module intentionally has no project imports so it can be used from both
``synthetic`` (which builds the price history) and ``delivery_compare``-adjacent
code without creating an import cycle.
"""

from __future__ import annotations

import hashlib
import re

_ID_LENGTH = 12


def _normalise(text: str) -> str:
    """Lower-case, trim, and collapse whitespace for a stable hash input."""
    return re.sub(r"\s+", " ", str(text).strip().lower())


def make_dish_id(source: str, name: str) -> str:
    """Return a deterministic id for a (restaurant, dish name) pair."""
    key = f"{_normalise(source)}|{_normalise(name)}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:_ID_LENGTH]
