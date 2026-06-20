"""Turn scraped menu items into a table and a downloadable CSV."""

from __future__ import annotations

from dataclasses import asdict

import pandas as pd

from .scrapers.base import MenuItem

_COLUMNS = ["name", "price", "category", "description"]


def items_to_dataframe(items: list[MenuItem]) -> pd.DataFrame:
    """Build a tidy DataFrame with a stable column order."""
    frame = pd.DataFrame([asdict(item) for item in items])
    if frame.empty:
        return pd.DataFrame(columns=_COLUMNS)
    return frame.reindex(columns=_COLUMNS)


def dataframe_to_csv_bytes(frame: pd.DataFrame) -> bytes:
    """Encode a DataFrame as UTF-8 CSV bytes for Streamlit's download button."""
    return frame.to_csv(index=False).encode("utf-8")
