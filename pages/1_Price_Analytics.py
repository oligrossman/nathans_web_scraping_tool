"""Streamlit page: observed price analytics.

Thin presentation layer - all data work lives in ``src/analytics.py`` and all
figures in ``src/charts.py``. This file only wires them to widgets.
"""

from __future__ import annotations

import math
from pathlib import Path

import pandas as pd
import streamlit as st

from src import analytics, auth, charts, delivery_compare, drilldown, high_low
from src.app_reload import reload_branding
from src.github_data import render_sync_banner
from src.enrichment.schema import GroupingDimension
from src.enrichment.store import DEFAULT_DB_PATH, list_markets
from src.history_archive import resolve_company_snapshots

_branding = reload_branding()
ANALYTICS_TAGLINE = _branding.ANALYTICS_TAGLINE
ANALYTICS_TITLE = _branding.ANALYTICS_TITLE
APP_TITLE = _branding.APP_TITLE
CONFIG_PAGE_TITLE = _branding.CONFIG_PAGE_TITLE
MARKET_LABEL = _branding.MARKET_LABEL
OWN_BRAND = _branding.OWN_BRAND
apply_sidebar_footer_logo = _branding.apply_sidebar_footer_logo
apply_sidebar_logo = _branding.apply_sidebar_logo
brand_css = _branding.brand_css
render_grouping_selectbox = _branding.render_grouping_selectbox
search_label = _branding.search_label

st.set_page_config(
    page_title=f"{ANALYTICS_TITLE} | {APP_TITLE}",
    page_icon="📊",
    layout="wide",
)

# Gate the page directly (Cloud serves pages/ files by URL without app.py).
auth.require_login()

# Wordmark pinned at the top of the sidebar; the bowl image is rendered lower
# down (between the shared controls and the headline metrics).
apply_sidebar_logo()
st.markdown(brand_css(), unsafe_allow_html=True)

PRICES_PATH = analytics.DEFAULT_ALL_COMPANY_PRICES_PATH

st.title(ANALYTICS_TITLE)
st.caption(ANALYTICS_TAGLINE)
render_sync_banner()

if not Path(PRICES_PATH).exists():
    st.warning(
        "No all-company prices file found. Open the **Data** page and click "
        "**Update price history** (or run `python -m scripts.generate_data`)."
    )
    st.stop()


@st.cache_data
def _load(path: str, _mtime: float):
    raw = analytics.load_history(path)
    return analytics.with_derived_market(raw)


history = _load(str(PRICES_PATH), PRICES_PATH.stat().st_mtime)
if OWN_BRAND not in history["source"].unique():
    st.error(
        f"No price history rows for **{OWN_BRAND}**. "
        f"Found sources: {sorted(history['source'].unique())}. "
        "Update it from the **Data** page (or run `python -m scripts.generate_data`)."
    )
    st.stop()
competitors = analytics.competitor_sources(history)

# Markets shown on the charts: the generic "Market" plus any saved custom ones.
if "selected_markets" not in st.session_state:
    st.session_state["selected_markets"] = [MARKET_LABEL]

saved_markets = list_markets()
saved_market_names = [market.name for market in saved_markets]

# Drop any previously-selected market that has since been deleted.
market_choices = [MARKET_LABEL, *saved_market_names]
st.session_state["selected_markets"] = [
    name for name in st.session_state["selected_markets"] if name in market_choices
]
selected_market_names = st.session_state["selected_markets"]
selected_custom_names = [n for n in selected_market_names if n != MARKET_LABEL]
show_generic_market = MARKET_LABEL in selected_market_names


@st.cache_data
def _global_price_max(path: str, _mtime: float) -> float:
    """Priciest single item across the whole market, ignoring every UI toggle.

    Spans the price history (real items only, not aggregated market rows) plus
    both snapshot sources, so the range slider's ceiling is stable no matter
    which companies, grouping or market a user has selected.
    """
    prices: list[float] = []
    raw = analytics.load_history(path)
    items = raw[~raw["source"].astype(str).str.startswith("Market")]
    if not items.empty:
        prices.append(float(items["price"].max()))
    comparison = delivery_compare.build_comparison()
    if not comparison.empty:
        prices.append(float(comparison[["restaurant", "delivery"]].to_numpy().max()))
    menu = high_low.build_menu_prices()
    if not menu.empty:
        prices.append(float(menu["price"].max()))
    return max(prices) if prices else 0.0


@st.cache_data
def _chart_history(
    path: str,
    _mtime: float,
    dimension_value: str,
    market_names: tuple[str, ...],
    db_mtime: float,
    price_lo: float,
    price_hi: float,
):
    raw = analytics.load_history(path)
    # Drop items outside the range before markets are rebuilt, so every market
    # series is recomputed from the surviving items only.
    raw = raw[raw["price"].between(price_lo, price_hi)]
    dimension = GroupingDimension(dimension_value)
    markets = [m for m in list_markets() if m.name in market_names]
    return analytics.prepare_chart_history(
        raw,
        dimension=dimension,
        custom_markets=markets,
    )


if not competitors:
    st.warning("No competitor companies in the price history yet.")
    st.stop()

# Global controls: company selection, grouping dimension and the price-range
# filter drive every tab. The grouping selector is bound to a shared session
# key, so it stays in sync with the same control on the Comparison
# Configuration page.
price_ceiling = _global_price_max(str(PRICES_PATH), PRICES_PATH.stat().st_mtime)
slider_max = float(math.ceil(price_ceiling)) or 1.0
# Seed and clamp the stored range before the widget is built (its only safe
# mutation point). Driving the slider purely from session state — rather than
# also passing ``value=`` — avoids Streamlit's "default value + Session State"
# warning while keeping a stale value inside the current ceiling.
if "price_range" not in st.session_state:
    st.session_state["price_range"] = (0.0, slider_max)
stored_lo, stored_hi = st.session_state["price_range"]
st.session_state["price_range"] = (
    max(0.0, float(stored_lo)),
    min(slider_max, float(stored_hi)),
)

def _sync_comparison_selection(options: list[str]) -> None:
    """Keep the company multiselect in step with the history file.

    ``default=`` applies only the first time a widget is created. A brand that
    arrives after Update price history would otherwise sit in the list unticked.
    Brands the user has already turned off stay off.
    """
    key = "comparison_companies"
    previous = st.session_state.get("_comparison_options")
    if key not in st.session_state or previous is None:
        st.session_state[key] = list(options)
    else:
        chosen = [company for company in st.session_state[key] if company in options]
        chosen.extend(company for company in options if company not in previous)
        st.session_state[key] = chosen
    st.session_state["_comparison_options"] = list(options)


# Shared controls live in the sidebar (they drive every tab).
with st.sidebar:
    _sync_comparison_selection(competitors)
    selected_companies = st.multiselect(
        "Comparison companies",
        competitors,
        format_func=search_label,
        key="comparison_companies",
        help=f"Drives every chart. {OWN_BRAND} is always shown; pick one or "
        "more companies to compare against. Search ignores apostrophes, so "
        "Rosa's can be found by typing Rosas.",
    )
    grouping_dimension = render_grouping_selectbox(
        help="Group every chart and drilldown by course (bucket), cuisine or dish "
        f"type. Shared with the {CONFIG_PAGE_TITLE} page.",
    )
    price_lo, price_hi = st.slider(
        "Price range (£)",
        min_value=0.0,
        max_value=slider_max,
        step=0.5,
        key="price_range",
        help="Exclude items priced outside this range from every metric and "
        "chart, so a few very expensive dishes can't distort the comparison. "
        "The ceiling is the priciest item in the whole market.",
    )

# Bowl image sits between the shared controls (above) and the headline metrics.
apply_sidebar_footer_logo()

dim = analytics.dimension_column(grouping_dimension)
dim_label = grouping_dimension.label


def _in_price_range(frame, price_col: str = "price"):
    """Rows whose ``price_col`` sits within the global price-range slider."""
    if frame.empty or price_col not in frame.columns:
        return frame
    return frame[frame[price_col].between(price_lo, price_hi)].reset_index(drop=True)

if not selected_companies:
    st.info(
        f"No comparison companies selected — showing {OWN_BRAND} on its own. "
        "Pick companies above to compare against them."
    )

db_mtime = DEFAULT_DB_PATH.stat().st_mtime if DEFAULT_DB_PATH.exists() else 0.0
chart_history = _chart_history(
    str(PRICES_PATH),
    PRICES_PATH.stat().st_mtime,
    grouping_dimension.value,
    tuple(selected_custom_names),
    db_mtime,
    price_lo,
    price_hi,
)
focus_options = analytics.focus_options_for_history(
    chart_history,
    dimension=grouping_dimension,
)

compare_sources = [OWN_BRAND, *selected_companies]
if show_generic_market:
    compare_sources.append(MARKET_LABEL)
compare_sources.extend(f"Market: {name}" for name in selected_custom_names)


# --- Drilldown navigation --------------------------------------------------
# Two independent selectboxes (Company and the active dimension) act as both the
# breadcrumb and the filter. Either can stay on "All …", so you can drill by
# dimension across the whole market without picking a company first. The deepest
# level that isn't "All" decides what the left-hand chart is grouped by.


def _sanitise_selection(key: str, options: list[str], default: str) -> None:
    """Reset a selectbox's stored value if it's no longer a valid option.

    Runs before the widget is instantiated (the only safe time to set its state),
    so a stale pick — e.g. a bucket value after switching to cuisine — falls back
    to the default instead of raising.
    """
    if st.session_state.get(key) not in options:
        st.session_state[key] = default


def _drilldown_nav(
    prefix: str,
    frame,
    *,
    dim: str,
    dim_label: str,
) -> tuple[str | None, str | None]:
    """Render Company + {dimension} filters and return the chosen selection.

    Returns ``(company, mid_value)`` where ``None`` means "all" for that axis.
    The dimension options follow the company scope (all companies when no
    company is picked), so both boxes stay usable at every level.
    """
    all_companies = "All companies"
    all_mid = f"All {dim_label.lower()}"

    companies = sorted(frame["company"].unique())
    company_options = [all_companies, *companies]
    company_key = f"{prefix}_company_sel"
    _sanitise_selection(company_key, company_options, all_companies)

    cols = st.columns([1, 1, 2])
    with cols[0]:
        company_choice = st.selectbox(
            "Company",
            company_options,
            format_func=search_label,
            key=company_key,
        )
    company = None if company_choice == all_companies else company_choice

    scope = frame if company is None else frame[frame["company"] == company]
    mid_values = sorted(v for v in scope[dim].dropna().unique())
    mid_options = [all_mid, *mid_values]
    mid_key = f"{prefix}_mid_sel"
    _sanitise_selection(mid_key, mid_options, all_mid)
    with cols[1]:
        mid_choice = st.selectbox(dim_label, mid_options, key=mid_key)
    mid = None if mid_choice == all_mid else mid_choice
    with cols[2]:
        st.caption(
            f"The left chart compares restaurants. Pick a {dim_label.lower()} "
            "to average that group only — the dishes sit on the right, so you "
            "can see what is driving the average. A company narrows those dishes."
        )
    return company, mid


def _signed_pounds(value: float) -> str:
    """Format a signed money delta, e.g. 1.2 -> '+£1.20', -0.5 -> '-£0.50'."""
    sign = "+" if value >= 0 else "-"
    return f"{sign}£{abs(value):.2f}"


def _signed_pct(value: float) -> str:
    """Format a signed percentage, e.g. 12.3 -> '+12.3%'."""
    return f"{value:+.1f}%"


def _plain_pounds(value: float) -> str:
    """Format an unsigned money amount, e.g. 1.2 -> '£1.20'."""
    return f"£{value:.2f}"


def _delivery_annotation(frame, by_abs: bool):
    """Signed markup label per row for the restaurant-vs-delivery dumbbell."""
    if by_abs:
        return frame["markup_abs"].map(_signed_pounds)
    return frame["markup_pct"].map(_signed_pct)


def _spread_annotation(frame, by_abs: bool):
    """Spread label per row for high/low group dumbbells (high − low)."""
    if by_abs:
        return frame["spread"].map(_plain_pounds)
    pct = (frame["spread"] / frame["low"].replace(0, pd.NA) * 100.0).fillna(0.0)
    return pct.map(lambda v: f"{v:.1f}%")


def _versus_average_annotation(frame, by_abs: bool):
    """Item price minus the group average.

    The figure is drawn in the margin past the orange (highest) dot, so the
    words have to say what it measures. A bare ``+£2.50`` reads as the high
    price.
    """
    signed = (
        frame["diff_abs"].map(_signed_pounds)
        if by_abs
        else frame["diff_pct"].map(_signed_pct)
    )
    return signed.map(lambda text: f"{text} vs avg")


def _apply_drill_filter(frame, company: str | None, mid: str | None, dim: str):
    """Subset an item-level frame to the current (company, dimension) selection."""
    out = frame
    if company is not None:
        out = out[out["company"] == company]
    if mid is not None:
        out = out[out[dim] == mid]
    return out.reset_index(drop=True)


_ALL_ITEMS = "All items (whole market)"


def _market_filter(prefix: str, frame, markets):
    """Optional saved-market filter shown above a drilldown; returns the subset.

    Markets are their own concept — a saved competitor benchmark, not a company —
    so they get a dedicated selector rather than sitting in the Company dropdown.
    """
    if not markets:
        return frame, None
    names = [m.name for m in markets]
    options = [_ALL_ITEMS, *names]
    key = f"{prefix}_market_sel"
    _sanitise_selection(key, options, _ALL_ITEMS)
    choice = st.selectbox(
        "Market",
        options,
        key=key,
        help=(
            "Filter every metric and chart below to a saved market (defined on "
            f"the {CONFIG_PAGE_TITLE} page). Markets are competitor-only."
        ),
    )
    if choice == _ALL_ITEMS:
        return frame, None
    definition = next(m for m in markets if m.name == choice)
    # A market is competitor-only, but Wagamama is always kept so it can be
    # benchmarked against the market subset.
    own = frame[frame["company"] == OWN_BRAND]
    others = analytics.filter_snapshot_by_market(
        frame[frame["company"] != OWN_BRAND], definition
    )
    return pd.concat([others, own], ignore_index=True), choice


# --- Headline KPIs ---------------------------------------------------------
# Rendered in the sidebar (beneath the logo) since they apply across every tab
# and free up space in the main area. They still follow the price-range slider:
# drop aggregated market rows (item-level metric) and keep only in-band items.
kpi_history = history[~history["source"].astype(str).str.startswith("Market")]
kpi_history = _in_price_range(kpi_history)
kpis = analytics.compute_kpis(kpi_history if not kpi_history.empty else history)
own_brand_latest = kpis.latest_avg_by_source.get(OWN_BRAND, 0.0)
with st.sidebar:
    st.divider()
    st.subheader("Headline metrics")
    st.metric("Overall CAGR", f"{kpis.overall_cagr_pct}%")
    st.metric("Total price change (5y)", f"{kpis.total_change_pct}%")
    st.metric(
        "Fastest-rising bucket",
        kpis.fastest_bucket,
        f"{kpis.fastest_bucket_change_pct}%",
    )
    if selected_companies:
        selected_latest = [
            kpis.latest_avg_by_source.get(c, 0.0) for c in selected_companies
        ]
        competitor_avg = sum(selected_latest) / len(selected_latest)
        peer_label = (
            selected_companies[0] if len(selected_companies) == 1 else "selected peers"
        )
        st.metric(
            f"{OWN_BRAND} avg vs {peer_label}",
            f"£{own_brand_latest:.2f}",
            f"{own_brand_latest - competitor_avg:+.2f} vs {peer_label}",
        )
    else:
        st.metric(f"{OWN_BRAND} latest avg", f"£{own_brand_latest:.2f}")

# Tab labels are kept static (no ``dim_label`` interpolation) so that changing
# the grouping dimension — or any other top-level control — doesn't rebuild the
# tabs widget and snap the user back to the first tab. The active dimension is
# surfaced in each tab's caption instead.
tab_buckets, tab_compare, tab_delivery, tab_high_low = st.tabs(
    [
        "By group",
        f"{OWN_BRAND} vs market",
        "Restaurant vs delivery",
        "High vs low",
    ]
)

# --- By grouping dimension -------------------------------------------------
with tab_buckets:
    st.caption(
        f"Grouped by **{dim_label}** (change it with 'Group charts by' above). "
        f"{OWN_BRAND} is shown first as the benchmark. Dates are the day the "
        "price was recorded. Each point averages dishes we can follow across "
        "dates, so a dish that appears only on the latest menu does not move "
        "the line. For each company: **left** — average price trend; "
        "**right** — change since the previous date."
    )
    bygroup_pct = st.toggle(
        "Show as % change",
        key="bygroup_pct",
        help="Switch the trend line from absolute £ to cumulative (compounded) % "
        "change since the first quarter — the natural view for projecting inflation.",
    )
    # Wagamama always leads the list, then the selected competitors. Per-chart
    # titles are suppressed (show_title=False) — the caption above and the company
    # heading below already say what each chart is, so the titles were dead space.
    for company in [OWN_BRAND, *selected_companies]:
        st.markdown(f"#### {company}")
        company_rows = chart_history[chart_history["source"] == company]
        if company_rows.empty:
            st.caption(
                "No trend yet. A dish has to be priced on at least two dates "
                "before it can show a price change."
            )
            continue
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                charts.bucket_line(
                    chart_history,
                    company,
                    as_pct_change=bygroup_pct,
                    include_total=True,
                    show_title=False,
                    dimension=grouping_dimension,
                ),
                use_container_width=True,
                key=f"bucket_line_{company}",
            )
        with right:
            st.plotly_chart(
                charts.pct_change_heatmap(
                    chart_history,
                    company,
                    include_total=True,
                    show_title=False,
                    dimension=grouping_dimension,
                ),
                use_container_width=True,
                key=f"bucket_heat_{company}",
            )

# --- Wagamama vs market ----------------------------------------------------
with tab_compare:
    st.caption(
        f"{OWN_BRAND} vs selected companies vs the market(s) you choose, grouped by "
        f"**{grouping_dimension.label}**."
    )
    st.multiselect(
        "Market benchmark(s) to compare against",
        options=market_choices,
        key="selected_markets",
        help=(
            f"Pick one or more markets to show as 'the market'. "
            f"'{MARKET_LABEL}' is the weighted average of all selected competitors; "
            "saved custom markets appear by their name. Markets always exclude "
            f"{OWN_BRAND}. Create new markets on the {CONFIG_PAGE_TITLE} page."
        ),
    )
    if not show_generic_market and not selected_custom_names:
        st.info("Pick at least one market benchmark to compare against.")

    compare_pct = st.toggle(
        "Show as % change",
        key="compare_pct",
        help="Switch every chart on this tab from absolute £ to cumulative "
        "(compounded) % change since the first quarter — the natural view for "
        "projecting inflation. Bars then show the total change over the period.",
    )

    # Top-level comparison first, then the more granular per-value breakdown below.
    st.subheader("Prices and focus trend")
    # Keep both controls above the columns so the two charts stay top-aligned
    # (a widget inside one column would push only that chart down).
    focus_col, toggle_col = st.columns(2, vertical_alignment="bottom")
    with focus_col:
        focus_bucket = st.selectbox("Focus value", focus_options, index=0)
    with toggle_col:
        show_latest_bars = st.toggle(
            "Show latest instead of average",
            value=True,
            key="compare_bar_latest",
            disabled=compare_pct,
            help=(
                "On: each bar is that company's own most recent price for the "
                f"group, so {OWN_BRAND} stays visible when its history ends "
                "before a competitor scrape. Off: each bar is the average of "
                "those prices over the whole period. Both are still an average "
                "across the dishes in the group."
            ),
        )
    left, right = st.columns(2)
    with left:
        st.plotly_chart(
            charts.single_bucket_comparison(
                chart_history,
                focus_bucket,
                sources=compare_sources,
                as_pct_change=compare_pct,
                dimension=grouping_dimension,
            ),
            use_container_width=True,
        )
    with right:
        st.plotly_chart(
            charts.latest_price_bars(
                chart_history,
                sources=compare_sources,
                as_pct_change=compare_pct,
                use_latest=show_latest_bars,
                dimension=grouping_dimension,
            ),
            use_container_width=True,
        )

    st.subheader(f"Breakdown by {grouping_dimension.label.lower()} over time")
    st.plotly_chart(
        charts.comparison_facets(
            chart_history,
            sources=compare_sources,
            include_total=True,
            as_pct_change=compare_pct,
            dimension=grouping_dimension,
        ),
        use_container_width=True,
    )

# --- Restaurant vs delivery ------------------------------------------------
with tab_delivery:
    st.caption(
        "Each restaurant is shown on its latest scraped menu and its latest "
        "delivery menu — not an average over the price history. The left chart "
        f"compares restaurants. Pick a {dim_label.lower()} to keep that "
        "comparison and list the dishes on the right. "
        f"{OWN_BRAND} uses its scraped Brighton restaurant menu and Deliveroo "
        f"menu. The red line is {OWN_BRAND}'s best fit of those delivery prices "
        "against restaurant price, shown on the distribution chart."
    )

    _snapshots = resolve_company_snapshots()
    snapshots_ready = any(
        coll.exists() and deliv.exists()
        for coll, deliv in _snapshots.values()
    )
    if not snapshots_ready:
        st.warning(
            "No delivery snapshots found. Scrape the delivery sites on the "
            "**Data** page (or run `python -m scripts.scrape_all`)."
        )
    else:

        @st.cache_data
        def _comparison():
            return delivery_compare.build_comparison()

        comparison = _comparison()
        comparison = comparison[
            comparison["company"].isin([OWN_BRAND, *selected_companies])
        ].reset_index(drop=True)
        # Always enrich so market filters (which use cuisine/dish_type) work even
        # when the active grouping dimension is the native bucket column.
        comparison = analytics.enrich_snapshot(comparison)
        # Global price band: judge an item by its restaurant (menu) price.
        comparison = _in_price_range(comparison, "restaurant")
        comparison, active_market = _market_filter(
            "delivery", comparison, saved_markets
        )
        if comparison.empty:
            st.warning(
                "No matched restaurant vs delivery items for the current selection."
            )
        else:
            company, mid = _drilldown_nav(
                "delivery", comparison, dim=dim, dim_label=dim_label
            )
            view = _apply_drill_filter(comparison, company, mid, dim)

            # KPIs and both charts reflect the current drilldown selection.
            market_prefix = f"{active_market} · " if active_market else ""
            scope_label = company or "All companies"
            k1, k2, k3, k4 = st.columns(4)
            if view.empty:
                st.info("No matched items for this selection.")
            else:
                view_companies = delivery_compare.company_summary(view)
                top_company = view_companies.iloc[0]
                dearest = view.sort_values(
                    "markup_pct", ascending=False
                ).iloc[0]
                k1.metric("Matched items", f"{len(view)}")
                k2.metric("Avg delivery markup", f"{view['markup_pct'].mean():.1f}%")
                k3.metric(
                    "Highest markup company",
                    top_company["company"],
                    f"{top_company['markup_pct']}%",
                )
                k4.metric(
                    "Biggest single markup",
                    dearest["item"],
                    f"{dearest['markup_pct']}%",
                )

            by_abs = not st.toggle(
                "Show markups as %",
                key="delivery_pct",
                help="Off: absolute £ markup. On: % increase. Drives both the "
                "labels and the sort order.",
            )
            sort_col = "markup_abs" if by_abs else "markup_pct"
            left_kind, right_kind = drilldown.charts_for(company, mid)
            overall_label = active_market or MARKET_LABEL
            menu_note = drilldown.latest_menu_note(comparison)
            delivery_highlight: str | None = None
            if left_kind == drilldown.RESTAURANTS:
                # A chosen group changes which dishes are averaged. It does not
                # replace the restaurants with those dishes.
                left_items = drilldown.items_for_restaurant_comparison(
                    comparison, group=mid, dim=dim
                )
                drill_frame = delivery_compare.company_summary(
                    left_items,
                    overall_label=overall_label,
                    overall_exclude=OWN_BRAND,
                    sort_by=sort_col,
                )
                label_col = "company"
                delivery_highlight = "is_benchmark"
                title = (
                    f"{market_prefix}{mid}: restaurant vs delivery by restaurant"
                    if mid is not None
                    else (
                        f"{market_prefix}Restaurant vs delivery by restaurant "
                        f"(+ {overall_label} average)"
                    )
                )
            else:
                drill_frame = delivery_compare.bucket_summary(
                    comparison, company, dim=dim, sort_by=sort_col
                )
                label_col = dim
                title = (
                    f"{market_prefix}{company}: restaurant vs delivery by "
                    f"{dim_label.lower()}"
                )

            if not drill_frame.empty:
                drill_frame = drill_frame.copy()
                drill_frame["annotation"] = _delivery_annotation(drill_frame, by_abs)
                if delivery_highlight and label_col in drill_frame.columns:
                    drill_frame[delivery_highlight] = (
                        drill_frame[label_col] == OWN_BRAND
                    )

            item_frame = pd.DataFrame()
            if right_kind == drilldown.ITEMS:
                item_frame = drilldown.items_for_drivers(
                    comparison, company=company, group=mid, dim=dim
                )
                if not item_frame.empty:
                    item_frame = item_frame.sort_values(
                        sort_col, ascending=False
                    ).reset_index(drop=True)
                    item_frame = item_frame.copy()
                    if item_frame["company"].nunique() > 1:
                        item_frame["item"] = (
                            item_frame["item"].astype(str)
                            + " ("
                            + item_frame["company"].astype(str)
                            + ")"
                        )
                    item_frame["annotation"] = _delivery_annotation(item_frame, by_abs)
                    item_frame["is_benchmark"] = item_frame["company"] == OWN_BRAND

            left, right = st.columns(2)
            with left:
                if not drill_frame.empty:
                    st.plotly_chart(
                        charts.drilldown_dumbbell(
                            drill_frame,
                            label_col,
                            title=title,
                            annotate_col="annotation",
                            highlight_col=delivery_highlight,
                            subtitle=menu_note,
                        ),
                        use_container_width=True,
                        key="delivery_drill",
                    )
            with right:
                if right_kind == drilldown.ITEMS and not item_frame.empty:
                    driver_scope = company or "All restaurants"
                    st.plotly_chart(
                        charts.drilldown_dumbbell(
                            item_frame,
                            "item",
                            title=(
                                f"{market_prefix}{driver_scope} / {mid}: "
                                "dishes driving the average"
                            ),
                            annotate_col="annotation",
                            highlight_col="is_benchmark",
                        ),
                        use_container_width=True,
                        key="delivery_items",
                    )
                elif right_kind == drilldown.DISTRIBUTION and not view.empty:
                    # Fit Wagamama on the same price band, but not on the
                    # competitor's rows. Omit the line when Wagamama is the
                    # restaurant on the chart — the dots are already that menu.
                    benchmark = None
                    if company != OWN_BRAND:
                        benchmark = comparison[comparison["company"] == OWN_BRAND]
                        if len(benchmark) < 2:
                            benchmark = None
                    st.plotly_chart(
                        charts.delivery_scatter(
                            view,
                            dim=dim,
                            dim_label=dim_label,
                            benchmark=benchmark,
                            subtitle=menu_note,
                        ),
                        use_container_width=True,
                    )

            with st.expander("Full matched item table"):
                st.dataframe(comparison, use_container_width=True, hide_index=True)
            st.download_button(
                "Download restaurant vs delivery (CSV)",
                data=comparison.to_csv(index=False).encode("utf-8"),
                file_name="restaurant_vs_delivery.csv",
                mime="text/csv",
            )

# --- High vs low -----------------------------------------------------------
with tab_high_low:
    st.caption(
        "Lowest, average and highest prices on each restaurant's latest scraped "
        f"menu, with {OWN_BRAND} always included. This is not an average over the "
        "price history. The left chart compares restaurants. Pick a "
        f"{dim_label.lower()} to keep that comparison and list the dishes on the "
        f"right: the diamond (red star for {OWN_BRAND}) is that dish's price, "
        "between the cheapest and dearest in the group, and the number beside "
        "the row is the gap versus the group average. With no "
        f"{dim_label.lower()} selected, the right chart is the percentile curve."
    )

    menu_ready = any(
        coll.exists() for coll, _ in resolve_company_snapshots().values()
    )
    if not menu_ready:
        st.warning(
            "No menu snapshots found. Scrape sites on the **Data** page "
            "(or run `python -m scripts.scrape_all`)."
        )
    else:

        @st.cache_data
        def _menu_prices():
            return high_low.build_menu_prices()

        menu = _menu_prices()
        menu = menu[
            menu["company"].isin([OWN_BRAND, *selected_companies])
        ].reset_index(drop=True)
        # Always enrich so market filters work regardless of grouping dimension.
        menu = analytics.enrich_snapshot(menu)
        menu = _in_price_range(menu, "price")
        menu, active_market = _market_filter("highlow", menu, saved_markets)
        if menu.empty:
            st.warning("No menu items for the current selection.")
        else:
            company, mid = _drilldown_nav(
                "highlow", menu, dim=dim, dim_label=dim_label
            )
            view = _apply_drill_filter(menu, company, mid, dim)

            market_prefix = f"{active_market} · " if active_market else ""
            scope_label = company or "All companies"
            k1, k2, k3, k4 = st.columns(4)
            if view.empty:
                st.info("No menu items for this selection.")
            else:
                view_companies = high_low.company_summary(view)
                widest = view_companies.iloc[0]
                cheapest = view.loc[view["price"].idxmin()]
                dearest = view.loc[view["price"].idxmax()]
                k1.metric("Companies", view["company"].nunique())
                k2.metric(
                    "Widest menu spread",
                    widest["company"],
                    f"£{widest['spread']:.2f}",
                )
                k3.metric(
                    "Cheapest item",
                    cheapest["item"],
                    f"£{cheapest['price']:.2f}",
                )
                k4.metric(
                    "Most expensive item",
                    dearest["item"],
                    f"£{dearest['price']:.2f}",
                )

            by_abs = not st.toggle(
                "Show differences as %",
                key="highlow_pct",
                help="Off: absolute £ difference. On: % difference. At item level "
                f"the number is that item's price minus the {dim_label.lower()} "
                "average. Drives both the labels and the sort order.",
            )
            spread_sort = "spread" if by_abs else "spread_pct"
            left_kind, right_kind = drilldown.charts_for(company, mid)
            overall_label = active_market or MARKET_LABEL
            menu_note = drilldown.latest_menu_note(menu)
            highlight_col: str | None = None
            if left_kind == drilldown.RESTAURANTS:
                left_items = drilldown.items_for_restaurant_comparison(
                    menu, group=mid, dim=dim
                )
                drill_frame = high_low.company_summary(
                    left_items,
                    overall_label=overall_label,
                    overall_exclude=OWN_BRAND,
                    sort_by=spread_sort,
                )
                if not drill_frame.empty:
                    drill_frame["annotation"] = _spread_annotation(drill_frame, by_abs)
                    drill_frame["is_benchmark"] = drill_frame["company"] == OWN_BRAND
                    highlight_col = "is_benchmark"
                label_col = "company"
                title = (
                    f"{market_prefix}{mid}: price spread by restaurant"
                    if mid is not None
                    else (
                        f"{market_prefix}Menu price spread by restaurant "
                        f"(+ {overall_label} average)"
                    )
                )
            else:
                drill_frame = high_low.bucket_summary(
                    menu, company, dim=dim, sort_by=spread_sort
                )
                if not drill_frame.empty:
                    drill_frame["annotation"] = _spread_annotation(drill_frame, by_abs)
                label_col = dim
                title = (
                    f"{market_prefix}{company}: price spread by "
                    f"{dim_label.lower()}"
                )

            item_frame = pd.DataFrame()
            if right_kind == drilldown.ITEMS:
                # Drivers of the restaurant average: one row per dish in the group.
                item_frame = high_low.item_detail(menu, company, mid, dim=dim)
                if not item_frame.empty:
                    item_frame["diff_abs"] = (
                        item_frame["price"] - item_frame["group_avg"]
                    ).round(2)
                    item_frame["diff_pct"] = (
                        (item_frame["price"] / item_frame["group_avg"] - 1.0) * 100.0
                    ).round(1)
                    rank_col = "diff_abs" if by_abs else "diff_pct"
                    item_frame["annotation"] = _versus_average_annotation(
                        item_frame, by_abs
                    )
                    item_frame = (
                        item_frame.assign(_rank=item_frame[rank_col].abs())
                        .sort_values("_rank", ascending=False)
                        .drop(columns="_rank")
                        .reset_index(drop=True)
                    )

            # The curve is the right chart only when no group is selected.
            # Wagamama stays its own line, or the comparison disappears the
            # moment a competitor is selected.
            curve_frame = pd.DataFrame()
            if right_kind == drilldown.DISTRIBUTION:
                comp_label = company if company else "Competitors"
                comp_rows = menu[menu["company"] != OWN_BRAND]
                if company == OWN_BRAND:
                    comp_rows = comp_rows.iloc[0:0]
                elif company is not None:
                    comp_rows = comp_rows[comp_rows["company"] == company]
                wag_rows = menu[menu["company"] == OWN_BRAND]
                curve_parts = []
                if not comp_rows.empty:
                    curve_parts.append(comp_rows.assign(series=comp_label))
                if not wag_rows.empty:
                    curve_parts.append(wag_rows.assign(series=OWN_BRAND))
                curve_frame = (
                    pd.concat(curve_parts, ignore_index=True) if curve_parts else view
                )

            left, right = st.columns(2)
            with left:
                if not drill_frame.empty:
                    st.plotly_chart(
                        charts.high_low_dumbbell(
                            drill_frame,
                            label_col,
                            title=title,
                            annotate_col="annotation" if "annotation" in drill_frame.columns else None,
                            highlight_col=highlight_col,
                            subtitle=menu_note,
                        ),
                        use_container_width=True,
                        key="highlow_drill",
                    )
            with right:
                if right_kind == drilldown.ITEMS and not item_frame.empty:
                    driver_scope = company or "All restaurants"
                    st.plotly_chart(
                        charts.high_low_dumbbell(
                            item_frame,
                            "item",
                            title=(
                                f"{market_prefix}{driver_scope} / {mid}: "
                                "dishes driving the average"
                            ),
                            annotate_col="annotation",
                            highlight_col="is_benchmark",
                            mid_label="Item price",
                            subtitle=(
                                "Number on the right = this dish's price minus "
                                f"the {dim_label.lower()} average"
                            ),
                        ),
                        use_container_width=True,
                        key="highlow_items",
                    )
                elif right_kind == drilldown.DISTRIBUTION and not curve_frame.empty:
                    st.plotly_chart(
                        charts.price_curve(
                            curve_frame,
                            series_col="series",
                            detail_col="company",
                            title=(
                                f"{market_prefix}{OWN_BRAND}: "
                                "price by percentile (low → high)"
                                if company == OWN_BRAND
                                else f"{market_prefix}{scope_label} vs {OWN_BRAND}: "
                                "price by percentile (low → high)"
                            ),
                            subtitle=menu_note,
                        ),
                        use_container_width=True,
                        key="highlow_curve",
                    )

            with st.expander("Full menu price table"):
                st.dataframe(menu, use_container_width=True, hide_index=True)

st.divider()
st.download_button(
    "Download price history (CSV)",
    data=history.to_csv(index=False).encode("utf-8"),
    file_name="all_company_prices.csv",
    mime="text/csv",
)
