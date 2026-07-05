"""Generate synthetic quarterly price history for the analytics layer.

Everything here is fabricated but plausible. We anchor on the *current*
scraped Nando's prices and back-cast 5 years of quarterly prices so that
older quarters are cheaper (prices rise over time). Alongside Nando's we
synthesise:

- Wagamama: our own brand, a distinct bespoke menu.

Market is *not* generated here; it is derived at load time in ``analytics.py``
as a restaurant-count-weighted average of competitor companies.

The output is a single tidy/long DataFrame (one row per observation), which
is the friendliest shape for pandas aggregation and Plotly charts.

Reproducibility: all randomness flows through one seeded ``numpy`` generator,
so regenerating the data yields identical results.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

SEED = 42
N_QUARTERS = 20  # 5 years of quarterly observations.

# The five board-level buckets every menu category collapses into.
BUCKETS = ("Starters", "Mains", "Sides", "Drinks", "Desserts")

from .branding import OWN_BRAND
from .enrichment.identity import make_dish_id

# Shared labels used across analytics and charts.
MARKET_LABEL = "Market whole"
TOTAL_LABEL = "Total"

# Maps menu category labels to board-level buckets (Nando's + Pizza Express).
CATEGORY_TO_BUCKET: dict[str, str] = {
    # Nando's
    "Starters": "Starters",
    "PERi-PERi Chicken": "Mains",
    "Burgers, Pittas, Wraps": "Mains",
    "Salads & Bowls": "Mains",
    "Sharing Platters": "Mains",
    "Veggie": "Mains",
    "Nandinos (Kids)": "Mains",
    "Sides": "Sides",
    "Dips & Extras": "Sides",
    "Drinks": "Drinks",
    "Desserts": "Desserts",
    # Pizza Express
    "Starters & Sides": "Starters",
    "Dough Balls": "Starters",
    "Meal Deals": "Mains",
    "🏆 Matchday Winners 🏆": "Mains",
    "PIZZA": "Mains",
    "ROMANA PIZZA": "Mains",
    "CLASSIC PIZZA": "Mains",
    "LEGGERA": "Mains",
    "GLUTEN-FREE": "Mains",
    "Pasta": "Mains",
    "Salads": "Mains",
    "Mains": "Mains",
    "Kids": "Mains",
    "Dips": "Sides",
    "Soft Drinks": "Drinks",
    "Pizza": "Mains",
    "Gluten-Free": "Mains",
}

# Nominal annual price growth per bucket - the headline market trend that the
# back-cast reverses. Drinks and Mains inflate fastest; Sides slowest.
BUCKET_ANNUAL_GROWTH: dict[str, float] = {
    "Starters": 0.050,
    "Mains": 0.065,
    "Sides": 0.040,
    "Drinks": 0.075,
    "Desserts": 0.045,
}

# Wagamama menu items. (item, bucket, current price).
WAGAMAMA_MENU: tuple[tuple[str, str, float], ...] = (
    ("Edamame", "Starters", 5.50),
    ("Chicken Gyoza", "Starters", 7.25),
    ("Duck Spring Rolls", "Starters", 7.95),
    ("Chicken Katsu Curry", "Mains", 14.95),
    ("Yaki Soba", "Mains", 13.50),
    ("Teriyaki Salmon Soba", "Mains", 16.95),
    ("Pad Thai", "Mains", 13.95),
    ("Firecracker Chicken", "Mains", 15.50),
    ("Steamed Rice", "Sides", 3.50),
    ("Prawn Crackers", "Sides", 4.25),
    ("Green Tea", "Drinks", 2.95),
    ("Fresh Juice", "Drinks", 4.50),
    ("Asahi Beer", "Drinks", 5.95),
    ("White Chocolate Cheesecake", "Desserts", 6.95),
    ("Mochi Ice Cream", "Desserts", 5.95),
)

# Wagamama positions premium on mains/desserts, keener on sides/drinks. Its own
# growth path runs a touch hotter than the market on food.
WAGAMAMA_ANNUAL_GROWTH: dict[str, float] = {
    "Starters": 0.055,
    "Mains": 0.070,
    "Sides": 0.035,
    "Drinks": 0.060,
    "Desserts": 0.060,
}

_QUARTERLY_NOISE_SD = 0.012  # Per-quarter idiosyncratic wobble.
_ITEM_GROWTH_SPREAD = 0.015  # Per-item deviation from its bucket's growth.


def quarter_end_dates(n: int, end: date | None = None) -> list[date]:
    """Return ``n`` ascending quarter-end dates ending at the latest quarter."""
    anchor = end or date.today()
    end_quarter = pd.Period(anchor, freq="Q")
    periods = pd.period_range(end=end_quarter, periods=n, freq="Q")
    return [p.to_timestamp(how="end").date() for p in periods]


def _annual_to_quarterly(annual_rate: float) -> float:
    """Convert an annual growth rate to its equivalent quarterly rate."""
    return (1.0 + annual_rate) ** 0.25 - 1.0


def _backcast(
    anchor_price: float, annual_growth: float, rng: np.random.Generator, n: int
) -> np.ndarray:
    """Back-cast ``n`` quarterly prices ending at ``anchor_price`` (newest).

    Walks backwards from the most recent price, dividing out a quarterly
    growth rate plus a little noise, so the series trends upward over time.
    Returned oldest-first to match ``quarter_end_dates``.
    """
    quarterly_growth = _annual_to_quarterly(annual_growth)
    prices = np.empty(n)
    prices[-1] = anchor_price
    for t in range(n - 1, 0, -1):
        step = quarterly_growth + rng.normal(0.0, _QUARTERLY_NOISE_SD)
        prices[t - 1] = prices[t] / (1.0 + max(step, -0.5))
    return np.round(prices, 2)


def _series_rows(
    *,
    source: str,
    item: str,
    bucket: str,
    anchor_price: float,
    annual_growth: float,
    dates: list[date],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Build the tidy rows for one item's (or benchmark's) price series."""
    item_growth = annual_growth + rng.uniform(-_ITEM_GROWTH_SPREAD, _ITEM_GROWTH_SPREAD)
    prices = _backcast(anchor_price, item_growth, rng, len(dates))
    return pd.DataFrame(
        {
            "date": dates,
            "source": source,
            "item": item,
            "dish_id": make_dish_id(source, item),
            "bucket": bucket,
            "price": prices,
        }
    )


def _generate_for_menu(
    *,
    source: str,
    menu: list[tuple[str, str, float]],
    growth_by_bucket: dict[str, float],
    dates: list[date],
    rng: np.random.Generator,
) -> pd.DataFrame:
    frames = [
        _series_rows(
            source=source,
            item=item,
            bucket=bucket,
            anchor_price=price,
            annual_growth=growth_by_bucket[bucket],
            dates=dates,
            rng=rng,
        )
        for item, bucket, price in menu
    ]
    return pd.concat(frames, ignore_index=True)


def _menu_df_to_history(
    source: str,
    menu_df: pd.DataFrame,
    *,
    dates: list[date],
    rng: np.random.Generator,
    growth_by_bucket: dict[str, float] | None = None,
) -> pd.DataFrame:
    growth = growth_by_bucket or BUCKET_ANNUAL_GROWTH
    priced = menu_df.dropna(subset=["price"]).copy()
    priced["bucket"] = priced["category"].map(CATEGORY_TO_BUCKET)
    priced = priced.dropna(subset=["bucket"])
    menu = list(zip(priced["name"], priced["bucket"], priced["price"].astype(float)))
    return _generate_for_menu(
        source=source,
        menu=menu,
        growth_by_bucket=growth,
        dates=dates,
        rng=rng,
    )


def generate_price_history(
    menu_df: pd.DataFrame,
    *,
    pizza_express_df: pd.DataFrame | None = None,
    seed: int = SEED,
) -> pd.DataFrame:
    """Generate the tidy price-history dataset for competitor companies + Wagamama.

    ``menu_df`` anchors Nando's. Optional ``pizza_express_df`` anchors Pizza Express.
    Market is derived at load time from ``company_weights.csv``.
    """
    rng = np.random.default_rng(seed)
    dates = quarter_end_dates(N_QUARTERS)

    nandos = _menu_df_to_history("Nando's", menu_df, dates=dates, rng=rng)
    own_brand = _generate_for_menu(
        source=OWN_BRAND,
        menu=list(WAGAMAMA_MENU),
        growth_by_bucket=WAGAMAMA_ANNUAL_GROWTH,
        dates=dates,
        rng=rng,
    )

    frames = [nandos, own_brand]
    if pizza_express_df is not None and not pizza_express_df.empty:
        frames.append(
            _menu_df_to_history("Pizza Express", pizza_express_df, dates=dates, rng=rng)
        )

    history = pd.concat(frames, ignore_index=True)
    return history.sort_values(["source", "bucket", "item", "date"]).reset_index(
        drop=True
    )


def save_history(history: pd.DataFrame, path: str) -> None:
    """Write the tidy price history to CSV."""
    history.to_csv(path, index=False)
