"""What the two charts show on High vs low and Restaurant vs delivery.

The left chart is the restaurant comparison. That is the question the page is
for: how does each restaurant's average compare?

Choosing a course (or any other group) does not replace those restaurants with
a list of dishes. It only changes which dishes go into each restaurant's
average. The dishes themselves move to the right chart, so you can see what is
pulling an average up or down.

Both charts use the latest scraped menu. They do not average the price history.
"""

from __future__ import annotations

import pandas as pd

# Left chart, then right chart.
RESTAURANTS = "restaurants"
GROUPS = "groups"
ITEMS = "items"
DISTRIBUTION = "distribution"


def charts_for(company: str | None, group: str | None) -> tuple[str, str]:
    """Return ``(left, right)`` chart kinds for the current filters.

    A chosen group keeps restaurants on the left and lists dishes on the right.
    With no group, the right chart stays the price distribution (the scatter or
    the percentile curve). A company on its own breaks that restaurant into its
    groups on the left.
    """
    if group is not None:
        return RESTAURANTS, ITEMS
    if company is not None:
        return GROUPS, DISTRIBUTION
    return RESTAURANTS, DISTRIBUTION


def items_for_restaurant_comparison(
    frame: pd.DataFrame,
    *,
    group: str | None,
    dim: str,
) -> pd.DataFrame:
    """Items that feed the left-hand restaurant averages.

    The company dropdown does not narrow this frame. A group does: every
    restaurant stays, and only the dishes in that group are averaged.
    """
    if frame.empty or group is None or dim not in frame.columns:
        return frame
    return frame.loc[frame[dim] == group].reset_index(drop=True)


def items_for_drivers(
    frame: pd.DataFrame,
    *,
    company: str | None,
    group: str | None,
    dim: str,
) -> pd.DataFrame:
    """Dishes for the right-hand chart: the company and group the user picked."""
    out = frame
    if company is not None and "company" in out.columns:
        out = out.loc[out["company"] == company]
    if group is not None and dim in out.columns:
        out = out.loc[out[dim] == group]
    return out.reset_index(drop=True)


def latest_menu_note(frame: pd.DataFrame) -> str:
    """Subtitle: these prices are the latest menu, not a history average."""
    fallback = (
        "Latest scraped menu for each restaurant, "
        "not an average over the price history."
    )
    if frame is None or frame.empty or "as_of" not in frame.columns:
        return fallback
    stamps = pd.to_datetime(frame["as_of"], errors="coerce").dropna().unique()
    if len(stamps) == 0:
        return fallback
    labels = [f"{pd.Timestamp(stamp).day} {pd.Timestamp(stamp):%b %Y}" for stamp in sorted(stamps)]
    if len(labels) == 1:
        return (
            f"Latest scraped menu ({labels[0]}), "
            "not an average over the price history."
        )
    return fallback
