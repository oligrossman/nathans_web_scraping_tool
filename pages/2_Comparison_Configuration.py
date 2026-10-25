"""Streamlit page: comparison configuration.

Controls that shape the Price Analysis charts: the grouping dimension, dish
attribute mappings (with user overrides), and custom market definitions. All
settings are shared with the analytics page via ``st.session_state`` and the
SQLite mappings database.
"""

from __future__ import annotations

import streamlit as st

from src import analytics, auth
from src.github_data import publish, render_sync_banner
from src.app_reload import reload_branding
from src.enrichment.config_csv import (
    apply_dish_plan,
    apply_market_plan,
    export_dishes,
    export_markets,
    plan_dishes,
    plan_markets,
)
from src.enrichment.schema import Cuisine, DishType, MarketDefinition
from src.enrichment.store import (
    DEFAULT_DB_PATH,
    delete_market,
    enum_options,
    get_resolved_mappings,
    init_db,
    list_markets,
    save_market,
    set_override,
)
from src.menu_buckets import BUCKETS

_branding = reload_branding()
APP_TITLE = _branding.APP_TITLE
CONFIG_PAGE_TITLE = _branding.CONFIG_PAGE_TITLE
OWN_BRAND = _branding.OWN_BRAND
apply_sidebar_branding = _branding.apply_sidebar_branding
brand_css = _branding.brand_css
search_label = _branding.search_label

st.set_page_config(
    page_title=f"{CONFIG_PAGE_TITLE} | {APP_TITLE}",
    page_icon="⚙️",
    layout="wide",
)

# Gate the page directly (Cloud serves pages/ files by URL without app.py).
auth.require_login()

apply_sidebar_branding()
st.markdown(brand_css(), unsafe_allow_html=True)

st.title(CONFIG_PAGE_TITLE)
st.caption(
    "Configure how the Price Analysis charts group and benchmark prices. "
    "All market benchmarks are competitor-only and never include Wagamama dishes."
)
render_sync_banner()

init_db()

# Competitors feed the "limit to companies" option on custom markets.
history_path = analytics.DEFAULT_ALL_COMPANY_PRICES_PATH
if history_path.exists():
    competitors = analytics.competitor_sources(analytics.load_history(history_path))
else:
    competitors = []

saved_markets = list_markets()
allowed_values = enum_options()


def _show_issues(issues: list) -> None:
    """Show one warning listing every invalid cell."""
    if not issues:
        return
    st.warning(f"{len(issues)} invalid field(s). Those rows were left unchanged.")
    st.dataframe(
        [issue.as_dict() for issue in issues],
        hide_index=True,
        use_container_width=True,
    )


def _plan_summary(added: int, updated: int, removed: int) -> str:
    return f"Add {added}, update {updated}, remove {removed}."


# --- Dish mappings ---------------------------------------------------------
st.subheader("Dish mappings")
st.caption(
    "Download the mappings, edit the CSV, and import it back. "
    "You can add a dish, delete a row, or override bucket, cuisine, dish type, "
    "vegetarian, and vegan. A dish already on file is matched by its dish_id. "
    "A new dish needs a blank dish_id, or one that matches the restaurant and item. "
    "A key column with a value outside the allowed list raises a warning and "
    "that row is not written. Rows missing from the file are removed. "
    "An empty file is rejected, so it cannot wipe the table."
)

mappings = get_resolved_mappings()
st.download_button(
    "Download dish mappings (CSV)",
    data=export_dishes(mappings).encode("utf-8-sig"),
    file_name="dish_mappings.csv",
    mime="text/csv",
)

with st.expander("Allowed values"):
    bucket_col, cuisine_col, dish_type_col = st.columns(3)
    bucket_col.markdown(
        "**Bucket**\n\n" + "\n".join(f"- {value}" for value in allowed_values["bucket"])
    )
    cuisine_col.markdown(
        "**Cuisine**\n\n"
        + "\n".join(f"- {value}" for value in allowed_values["cuisine"])
    )
    dish_type_col.markdown(
        "**Dish type**\n\n"
        + "\n".join(f"- {value}" for value in allowed_values["dish_type"])
    )
    if competitors:
        st.caption(
            "Market companies, when set, must be one of: " + ", ".join(competitors) + "."
        )
    st.caption("Vegetarian and vegan are true or false.")

if mappings.empty:
    st.warning(
        "No dish mappings found. Run **Update price history** then **Run enrichment** "
        "on the **Data** page, or run `python -m scripts.enrich_menus` "
        "(requires `OPENAI_API_KEY`). You can still import a CSV."
    )
else:
    column_config = {
        "dish_id": st.column_config.TextColumn("ID", disabled=True),
        "source": st.column_config.TextColumn("Restaurant", disabled=True),
        "name": st.column_config.TextColumn("Item", disabled=True),
        "bucket": st.column_config.SelectboxColumn(
            "Bucket", options=allowed_values["bucket"]
        ),
        "cuisine": st.column_config.SelectboxColumn(
            "Cuisine", options=allowed_values["cuisine"]
        ),
        "dish_type": st.column_config.SelectboxColumn(
            "Dish type", options=allowed_values["dish_type"]
        ),
        "is_vegetarian": st.column_config.CheckboxColumn("Vegetarian"),
        "is_vegan": st.column_config.CheckboxColumn("Vegan"),
        "is_override": st.column_config.CheckboxColumn(
            "Override active", disabled=True
        ),
    }
    edited = st.data_editor(
        mappings,
        column_config=column_config,
        hide_index=True,
        use_container_width=True,
        key="mapping_editor",
    )

    if st.button("Save mapping overrides", type="primary"):
        for _, row in edited.iterrows():
            original = mappings[mappings["dish_id"] == row["dish_id"]].iloc[0]
            changed = any(
                row[col] != original[col]
                for col in (
                    "bucket",
                    "cuisine",
                    "dish_type",
                    "is_vegetarian",
                    "is_vegan",
                )
            )
            if changed:
                set_override(
                    row["dish_id"],
                    bucket=row["bucket"],
                    cuisine=row["cuisine"],
                    dish_type=row["dish_type"],
                    is_vegetarian=bool(row["is_vegetarian"]),
                    is_vegan=bool(row["is_vegan"]),
                )
        publish("Save mapping overrides")
        st.cache_data.clear()
        st.success("Overrides saved.")
        st.rerun()

dish_outcome = st.session_state.pop("dish_import_outcome", None)
if dish_outcome is not None and dish_outcome.structural_error is None:
    unchanged = ""
    if dish_outcome.issues:
        unchanged = (
            f" {len(dish_outcome.issues)} invalid field(s) were left unchanged."
        )
    st.success(
        "Dish import applied. "
        + _plan_summary(
            dish_outcome.added, dish_outcome.updated, dish_outcome.removed_count
        )
        + unchanged
    )

dish_file = st.file_uploader("Import dish mappings CSV", type=["csv"], key="dish_csv")
if dish_file is not None:
    dish_plan = plan_dishes(dish_file.getvalue(), str(DEFAULT_DB_PATH))
    if dish_plan.structural_error:
        st.warning(dish_plan.structural_error)
    else:
        st.caption(
            _plan_summary(dish_plan.added, dish_plan.updated, dish_plan.removed_count)
        )
        if dish_plan.removed:
            preview = ", ".join(
                f"{source} — {name}"
                for _dish_id, source, name in dish_plan.removed[:20]
            )
            extra = ""
            if len(dish_plan.removed) > 20:
                extra = f" and {len(dish_plan.removed) - 20} more"
            st.warning(
                "These dishes are missing from the file and will be removed: "
                f"{preview}{extra}."
            )
        _show_issues(dish_plan.issues)
        if st.button("Apply dish import", type="primary", key="apply_dish_csv"):
            apply_dish_plan(dish_plan, str(DEFAULT_DB_PATH))
            publish("Import dish mappings")
            st.session_state["dish_import_outcome"] = dish_plan
            st.cache_data.clear()
            st.rerun()

st.divider()

# --- Custom markets --------------------------------------------------------
st.subheader("Define a custom market")
st.caption(
    "Markets are competitor benchmarks — Wagamama dishes are always excluded. "
    f"Saved markets can then be plotted on the **{OWN_BRAND} vs market** tab."
)

with st.form("market_form"):
    market_name = st.text_input("Market name", placeholder="Asian noodle dishes")
    col1, col2 = st.columns(2)
    with col1:
        filter_cuisines = st.multiselect(
            "Cuisines",
            [member.value for member in Cuisine],
        )
        filter_dish_types = st.multiselect(
            "Dish types",
            [member.value for member in DishType],
        )
    with col2:
        filter_buckets = st.multiselect("Buckets", list(BUCKETS))
        filter_companies = st.multiselect(
            "Limit to companies (optional)",
            competitors,
            format_func=search_label,
        )
    submitted = st.form_submit_button("Save market")

if submitted:
    if not market_name.strip():
        st.error("Enter a market name.")
    else:
        definition = MarketDefinition(
            name=market_name.strip(),
            cuisines=filter_cuisines,
            dish_types=filter_dish_types,
            companies=filter_companies,
            buckets=filter_buckets,
        )
        save_market(definition)
        publish("Save market")
        st.cache_data.clear()
        st.success(f"Saved market **{definition.name}**.")
        st.rerun()

st.markdown("#### Saved markets")
st.download_button(
    "Download markets (CSV)",
    data=export_markets(saved_markets).encode("utf-8-sig"),
    file_name="markets.csv",
    mime="text/csv",
)
st.caption(
    "List cells are comma-separated. A blank cell means no filter on that field. "
    "The same import rules apply: invalid values are warned and left unchanged, "
    "and a market missing from the file is removed."
)

if saved_markets:
    for market in saved_markets:
        cols = st.columns([4, 1])
        with cols[0]:
            st.markdown(f"**{market.name}** — {market.to_dict()}")
        with cols[1]:
            if st.button("Delete", key=f"delete_market_{market.name}"):
                delete_market(market.name)
                publish("Delete market")
                st.cache_data.clear()
                st.rerun()
else:
    st.caption("No saved markets yet.")

market_outcome = st.session_state.pop("market_import_outcome", None)
if market_outcome is not None and market_outcome.structural_error is None:
    unchanged = ""
    if market_outcome.issues:
        unchanged = (
            f" {len(market_outcome.issues)} invalid field(s) were left unchanged."
        )
    st.success(
        "Market import applied. "
        + _plan_summary(
            market_outcome.added,
            market_outcome.updated,
            market_outcome.removed_count,
        )
        + unchanged
    )

market_file = st.file_uploader("Import markets CSV", type=["csv"], key="market_csv")
if market_file is not None:
    market_plan = plan_markets(
        market_file.getvalue(),
        str(DEFAULT_DB_PATH),
        companies=set(competitors),
    )
    if market_plan.structural_error:
        st.warning(market_plan.structural_error)
    else:
        st.caption(
            _plan_summary(
                market_plan.added, market_plan.updated, market_plan.removed_count
            )
        )
        if market_plan.removed:
            st.warning(
                "These markets are missing from the file and will be removed: "
                + ", ".join(market_plan.removed)
                + "."
            )
        _show_issues(market_plan.issues)
        if st.button("Apply market import", type="primary", key="apply_market_csv"):
            apply_market_plan(market_plan, str(DEFAULT_DB_PATH))
            publish("Import markets")
            st.session_state["market_import_outcome"] = market_plan
            st.cache_data.clear()
            st.rerun()
