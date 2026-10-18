"""DataFrame → JSON records helpers for MCP tool responses."""

from __future__ import annotations

from typing import Any

import pandas as pd

DEFAULT_LIMIT = 50
HARD_MAX = 200


def clamp_limit(limit: int | None = None) -> int:
    """Clamp a requested row limit to ``[1, HARD_MAX]`` (default 50)."""
    if limit is None:
        return DEFAULT_LIMIT
    return max(1, min(int(limit), HARD_MAX))


def dataframe_to_records(
    frame: pd.DataFrame,
    *,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Convert a DataFrame to JSON-safe records, capped at ``limit`` rows."""
    if frame is None or frame.empty:
        return []

    capped = clamp_limit(limit)
    out = frame.head(capped).copy()

    for col in out.columns:
        if pd.api.types.is_datetime64_any_dtype(out[col]):
            out[col] = out[col].dt.strftime("%Y-%m-%d")
        elif pd.api.types.is_float_dtype(out[col]):
            out[col] = out[col].round(2)

    records = out.to_dict(orient="records")
    for row in records:
        for key, value in list(row.items()):
            if pd.isna(value):
                row[key] = None
            elif hasattr(value, "item"):
                # numpy scalars → plain Python
                row[key] = value.item()
    return records
