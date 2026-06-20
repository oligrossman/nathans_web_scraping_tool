"""Streamlit page: synthetic price analytics.

Thin presentation layer - all data work lives in ``src/analytics.py`` and all
figures in ``src/charts.py``. This file only wires them to widgets.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from src import analytics, charts, delivery_compare, high_low
from src.branding import (
    ANALYTICS_TAGLINE,
    ANALYTICS_TITLE,
    APP_TITLE,
    OWN_BRAND,
    apply_sidebar_branding,
    brand_css,
)
from src.synthetic import BUCKETS, TOTAL_LABEL

st.set_page_config(
    page_title=f"{ANALYTICS_TITLE} | {APP_TITLE}",
    page_icon="📊",
    layout="wide",
)

apply_sidebar_branding()
st.markdown(brand_css(), unsafe_allow_html=True)

HISTORY_PATH = analytics.DEFAULT_HISTORY_PATH

st.title(ANALYTICS_TITLE)
st.caption(ANALYTICS_TAGLINE)

if not Path(HISTORY_PATH).exists():
    st.warning(
        "No price history found. Generate it first by running "
        "`python -m scripts.generate_data` from the project root."
    )
    st.stop()


@st.cache_data
def _load(path: str, _mtime: float):
    raw = analytics.load_history(path)
    return analytics.with_derived_market(raw)


history = _load(str(HISTORY_PATH), HISTORY_PATH.stat().st_mtime)
if OWN_BRAND not in history["source"].unique():
    st.error(
        f"No price history rows for **{OWN_BRAND}**. "
        f"Found sources: {sorted(history['source'].unique())}. "
        "Re-run `python -m scripts.generate_data` to regenerate the dataset."
    )
    st.stop()
competitors = analytics.competitor_sources(history)
focus_options = [TOTAL_LABEL, *BUCKETS]

if not competitors:
    st.warning("No competitor companies in the price history yet.")
    st.stop()

# Single source of truth: every chart compares Wagamama against the companies
# picked here. Wagamama is always shown.
selected_companies = st.multiselect(
    "Comparison companies",
    competitors,
    default=competitors,
    help=f"Drives every chart below. {OWN_BRAND} is always shown; pick one or more "
    "companies to compare against.",
)
if not selected_companies:
    st.info("Select at least one company to compare.")
    st.stop()
compare_sources = [OWN_BRAND, *selected_companies, "Market"]


# --- Drilldown helpers (click a chart row to drill down) -------------------


def _init_drilldown(prefix: str) -> None:
    defaults = {
        f"{prefix}_level": "company",
        f"{prefix}_company": None,
        f"{prefix}_bucket": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _reset_drilldown(prefix: str) -> None:
    st.session_state[f"{prefix}_level"] = "company"
    st.session_state[f"{prefix}_company"] = None
    st.session_state[f"{prefix}_bucket"] = None


def _drilldown_breadcrumb(prefix: str) -> None:
    level = st.session_state[f"{prefix}_level"]
    company = st.session_state[f"{prefix}_company"]
    bucket = st.session_state[f"{prefix}_bucket"]

    cols = st.columns([1, 1, 1, 2])
    with cols[0]:
        if st.button("All companies", key=f"{prefix}_crumb_all", disabled=level == "company"):
            _reset_drilldown(prefix)
    with cols[1]:
        if st.button(
            f"Company: {company or '—'}",
            key=f"{prefix}_crumb_co",
            disabled=company is None or level == "company",
        ):
            st.session_state[f"{prefix}_level"] = "bucket"
            st.session_state[f"{prefix}_bucket"] = None
    with cols[2]:
        st.button(
            f"Bucket: {bucket or '—'}",
            key=f"{prefix}_crumb_bucket",
            disabled=bucket is None or level == "item",
        )
    with cols[3]:
        st.caption("Click a row in the chart below to drill down.")


def _handle_drilldown_click(event, labels_at_level: list[str], prefix: str) -> None:
    if event is None:
        return
    selection = getattr(event, "selection", None)
    if selection is None and isinstance(event, dict):
        selection = event.get("selection")
    if not selection:
        return
    points = (
        selection.get("points")
        if isinstance(selection, dict)
        else getattr(selection, "points", None)
    )
    if not points:
        return
    point = points[0]
    label = point.get("y") if isinstance(point, dict) else getattr(point, "y", None)
    if label is None or str(label) not in labels_at_level:
        return
    level = st.session_state[f"{prefix}_level"]
    if level == "company":
        st.session_state[f"{prefix}_company"] = str(label)
        st.session_state[f"{prefix}_level"] = "bucket"
    elif level == "bucket":
        st.session_state[f"{prefix}_bucket"] = str(label)
        st.session_state[f"{prefix}_level"] = "item"


# --- Headline KPIs ---------------------------------------------------------
kpis = analytics.compute_kpis(history)
st.subheader("Headline metrics")
col1, col2, col3, col4 = st.columns(4)
col1.metric("Overall CAGR", f"{kpis.overall_cagr_pct}%")
col2.metric("Total price change (5y)", f"{kpis.total_change_pct}%")
col3.metric(
    "Fastest-rising bucket",
    kpis.fastest_bucket,
    f"{kpis.fastest_bucket_change_pct}%",
)
own_brand_latest = kpis.latest_avg_by_source.get(OWN_BRAND, 0.0)
selected_latest = [kpis.latest_avg_by_source.get(c, 0.0) for c in selected_companies]
competitor_avg = sum(selected_latest) / len(selected_latest)
peer_label = selected_companies[0] if len(selected_companies) == 1 else "selected peers"
col4.metric(
    f"{OWN_BRAND} avg vs {peer_label}",
    f"£{own_brand_latest:.2f}",
    f"{own_brand_latest - competitor_avg:+.2f} vs {peer_label}",
)

st.divider()

tab_buckets, tab_compare, tab_delivery, tab_high_low = st.tabs(
    [
        "By bucket",
        f"{OWN_BRAND} vs market",
        "Collection vs delivery",
        "High vs low",
    ]
)

# --- By bucket -------------------------------------------------------------
with tab_buckets:
    st.caption(f"One panel per selected company. {OWN_BRAND} shown separately below.")
    for company in [*selected_companies, OWN_BRAND]:
        st.markdown(f"#### {company}")
        left, right = st.columns(2)
        with left:
            st.plotly_chart(
                charts.bucket_line(
                    history, company, indexed=False, include_total=True
                ),
                use_container_width=True,
                key=f"bucket_abs_{company}",
            )
        with right:
            st.plotly_chart(
                charts.bucket_line(
                    history, company, indexed=True, include_total=True
                ),
                use_container_width=True,
                key=f"bucket_idx_{company}",
            )
        st.plotly_chart(
            charts.pct_change_heatmap(history, company, include_total=True),
            use_container_width=True,
            key=f"bucket_heat_{company}",
        )

# --- Wagamama vs market ----------------------------------------------------
with tab_compare:
    st.caption(f"{OWN_BRAND} vs the selected companies vs Market (set above).")
    st.subheader("Breakdown by bucket")
    st.plotly_chart(
        charts.comparison_facets(
            history,
            sources=compare_sources,
            include_total=True,
        ),
        use_container_width=True,
    )

    left, right = st.columns(2)
    with left:
        focus_bucket = st.selectbox("Focus bucket", focus_options, index=0)
        st.plotly_chart(
            charts.single_bucket_comparison(
                history,
                focus_bucket,
                sources=compare_sources,
            ),
            use_container_width=True,
        )
    with right:
        st.plotly_chart(
            charts.latest_price_bars(history, sources=compare_sources),
            use_container_width=True,
        )

# --- Collection vs delivery ------------------------------------------------
with tab_delivery:
    st.caption(
        "Real prices scraped from each company's collection menu and the same "
        "restaurant on Deliveroo (Camden postcode). Click a row in the chart "
        "to drill down: Company → Bucket → Item."
    )

    snapshots_ready = any(
        coll.exists() and deliv.exists()
        for coll, deliv in delivery_compare.COMPANY_SNAPSHOTS.values()
    )
    if not snapshots_ready:
        st.warning(
            "No delivery snapshots found. Run `python -m scripts.generate_data` "
            "to scrape them."
        )
    else:
        _init_drilldown("delivery")

        @st.cache_data
        def _comparison():
            return delivery_compare.build_comparison()

        comparison = _comparison()
        comparison = comparison[
            comparison["company"].isin(selected_companies)
        ].reset_index(drop=True)
        if comparison.empty:
            st.warning(
                "No matched collection vs delivery items for the selected companies."
            )
        else:
            company_totals = delivery_compare.company_summary(comparison)
            summary = delivery_compare.bucket_summary(comparison)
            avg_markup = comparison["markup_pct"].mean()
            dearest = comparison.iloc[0]
            top_company = company_totals.iloc[0]

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Matched items", f"{len(comparison)}")
            k2.metric("Avg delivery markup", f"{avg_markup:.1f}%")
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

            left, right = st.columns(2)
            with left:
                st.plotly_chart(
                    charts.delivery_bucket_bars(summary), use_container_width=True
                )
            with right:
                st.plotly_chart(
                    charts.delivery_scatter(comparison), use_container_width=True
                )

            _drilldown_breadcrumb("delivery")
            level = st.session_state["delivery_level"]

            if level == "company":
                drill_frame = company_totals
                label_col = "company"
                title = "Collection vs delivery by company (click a row to drill down)"
                labels = drill_frame["company"].tolist()
            elif level == "bucket":
                company = st.session_state["delivery_company"]
                drill_frame = delivery_compare.bucket_summary(comparison, company)
                label_col = "bucket"
                title = f"{company}: collection vs delivery by bucket (click a row to drill down)"
                labels = drill_frame["bucket"].tolist()
            else:
                company = st.session_state["delivery_company"]
                bucket = st.session_state["delivery_bucket"]
                drill_frame = (
                    comparison[
                        (comparison["company"] == company)
                        & (comparison["bucket"] == bucket)
                    ]
                    .sort_values("markup_pct", ascending=False)
                    .reset_index(drop=True)
                )
                label_col = "item"
                title = f"{company} / {bucket}: collection vs delivery by item"
                labels = drill_frame["item"].tolist()

            fig = charts.drilldown_dumbbell(drill_frame, label_col, title=title)
            selection = st.plotly_chart(
                fig,
                use_container_width=True,
                on_select="rerun" if level != "item" else "ignore",
                key=(
                    f"delivery_{level}_"
                    f"{st.session_state['delivery_company']}_"
                    f"{st.session_state['delivery_bucket']}"
                ),
            )
            if level != "item":
                _handle_drilldown_click(selection, labels, "delivery")

            with st.expander("Full matched item table"):
                st.dataframe(comparison, use_container_width=True, hide_index=True)
            st.download_button(
                "Download collection vs delivery (CSV)",
                data=comparison.to_csv(index=False).encode("utf-8"),
                file_name="collection_vs_delivery.csv",
                mime="text/csv",
            )

# --- High vs low -----------------------------------------------------------
with tab_high_low:
    st.caption(
        "Lowest, average and highest menu prices from collection snapshots. "
        "At item level the average dot shows each item's price within its bucket's "
        "min–max range. Click a row in the chart to drill down."
    )

    menu_ready = any(
        coll.exists() for coll, _ in delivery_compare.COMPANY_SNAPSHOTS.values()
    )
    if not menu_ready:
        st.warning("No menu snapshots found. Run `python -m scripts.generate_data`.")
    else:
        _init_drilldown("highlow")

        @st.cache_data
        def _menu_prices():
            return high_low.build_menu_prices()

        menu = _menu_prices()
        menu = menu[menu["company"].isin(selected_companies)].reset_index(drop=True)
        if menu.empty:
            st.warning("No menu price data for the selected companies.")
        else:
            hl_companies = high_low.company_summary(menu)
            widest = hl_companies.iloc[0]

            k1, k2, k3, k4 = st.columns(4)
            k1.metric("Companies", len(hl_companies))
            k2.metric(
                "Widest menu spread",
                widest["company"],
                f"£{widest['spread']:.2f}",
            )
            k3.metric(
                "Cheapest item (all menus)",
                f"£{menu['price'].min():.2f}",
            )
            k4.metric(
                "Most expensive item (all menus)",
                f"£{menu['price'].max():.2f}",
            )

            _drilldown_breadcrumb("highlow")
            level = st.session_state["highlow_level"]

            if level == "company":
                drill_frame = hl_companies
                label_col = "company"
                title = "Menu price spread by company (click a row to drill down)"
                labels = drill_frame["company"].tolist()
            elif level == "bucket":
                company = st.session_state["highlow_company"]
                drill_frame = high_low.bucket_summary(menu, company)
                label_col = "bucket"
                title = f"{company}: price spread by bucket (click a row to drill down)"
                labels = drill_frame["bucket"].tolist()
            else:
                company = st.session_state["highlow_company"]
                bucket = st.session_state["highlow_bucket"]
                drill_frame = high_low.item_detail(menu, company, bucket)
                label_col = "item"
                title = f"{company} / {bucket}: item prices within bucket range"
                labels = drill_frame["item"].tolist()

            fig = charts.high_low_dumbbell(drill_frame, label_col, title=title)
            selection = st.plotly_chart(
                fig,
                use_container_width=True,
                on_select="rerun" if level != "item" else "ignore",
                key=(
                    f"highlow_{level}_"
                    f"{st.session_state['highlow_company']}_"
                    f"{st.session_state['highlow_bucket']}"
                ),
            )
            if level != "item":
                _handle_drilldown_click(selection, labels, "highlow")

            with st.expander("Full menu price table"):
                st.dataframe(menu, use_container_width=True, hide_index=True)

st.divider()
st.download_button(
    "Download price history (CSV)",
    data=history.to_csv(index=False).encode("utf-8"),
    file_name="price_history.csv",
    mime="text/csv",
)
