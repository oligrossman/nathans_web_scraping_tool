"""Menu data page — scrape menus into snapshots and build the analytics dataset."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import streamlit as st

from datetime import datetime

from src import auth, history_archive, scrape_log
from src.branding import APP_TAGLINE, APP_TITLE, SCRAPE_SECTION, apply_sidebar_branding, brand_css
from src.enrichment.store import DEFAULT_DB_PATH
from src.export import dataframe_to_csv_bytes, items_to_dataframe
from src.scrapers.registry import SCRAPERS, get_scraper, snapshot_paths_by_site

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SNAPSHOT_BY_SITE = snapshot_paths_by_site(data_dir=DATA_DIR)
_MAX_WORKERS = 4

# Gate this page directly too: on Streamlit Cloud each file under ``pages/`` is
# reachable by URL without passing through ``app.py``, so the entrypoint check
# alone isn't enough. ``require_login`` returns immediately once authenticated.
auth.require_login()

apply_sidebar_branding()
st.markdown(brand_css(), unsafe_allow_html=True)
st.title(APP_TITLE)
st.caption(APP_TAGLINE)


def _slug(site_name: str) -> str:
    return "".join(c if c.isalnum() else "_" for c in site_name).lower()


def _snapshot_path(site_name: str) -> Path:
    """Canonical snapshot path the rest of the pipeline reads for this site."""
    return SNAPSHOT_BY_SITE[site_name]


def _scrape_one(site_name: str):
    """Run a single scraper and return its menu as a DataFrame."""
    items = get_scraper(site_name).scrape()
    return items_to_dataframe(items)


def run_scrapes(site_names: list[str]) -> list[dict]:
    """Scrape every selected site concurrently, preserving input order."""
    results: dict[str, dict] = {}
    with ThreadPoolExecutor(max_workers=min(_MAX_WORKERS, len(site_names))) as executor:
        futures = {executor.submit(_scrape_one, name): name for name in site_names}
        for future in as_completed(futures):
            name = futures[future]
            try:
                frame = future.result()
                results[name] = {
                    "site": name,
                    "ok": True,
                    "count": len(frame),
                    "frame": frame,
                    "error": None,
                }
            except Exception as exc:  # surfaced per-site instead of failing the batch
                results[name] = {
                    "site": name,
                    "ok": False,
                    "count": 0,
                    "frame": None,
                    "error": str(exc),
                }
    return [results[name] for name in site_names]


def persist_results(results: list[dict]) -> None:
    """Save each scrape to its canonical snapshot and today's dated archive.

    The canonical ``data/*_snapshot.csv`` is the *latest* menu (used by the
    delivery and high/low comparisons). A dated copy is also filed under
    ``data/scrapes/<YYYY-MM-DD>/`` so **Update price history** can accumulate a
    real time series across scrapes (and you can delete a day to drop it).
    """
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    when = datetime.now().date()
    for result in results:
        if not result["ok"]:
            continue
        path = _snapshot_path(result["site"])
        path.parent.mkdir(parents=True, exist_ok=True)
        result["frame"].to_csv(path, index=False)
        archived = history_archive.archive_snapshot(
            result["frame"], path.name, when=when
        )
        result["path"] = str(path.relative_to(DATA_DIR.parent))
        result["archived"] = str(archived.relative_to(DATA_DIR.parent))
        scrape_log.record_scrape(result["site"], result["count"])


# --- Scrape status ---------------------------------------------------------
st.subheader("Scrape status")
st.caption("When each site was last scraped — avoid re-scraping fresh data.")
st.dataframe(
    scrape_log.status_table(list(SCRAPERS)),
    use_container_width=True,
    hide_index=True,
)

st.divider()

# --- Selection + scrape ----------------------------------------------------
st.subheader(SCRAPE_SECTION)


def _toggle_select_all() -> None:
    st.session_state["scrape_selection"] = (
        list(SCRAPERS) if st.session_state["scrape_select_all"] else []
    )


st.checkbox("Select all sites", key="scrape_select_all", on_change=_toggle_select_all)
selected = st.multiselect(
    "Choose websites to scrape",
    options=list(SCRAPERS),
    key="scrape_selection",
)

if st.button("Scrape selected", type="primary", disabled=not selected):
    with st.spinner(f"Scraping {len(selected)} site(s) concurrently..."):
        results = run_scrapes(selected)
        persist_results(results)
    st.session_state["scrape_results"] = results
    st.rerun()

# --- Results ---------------------------------------------------------------
results = st.session_state.get("scrape_results")
if results:
    st.subheader("Results")
    for result in results:
        site = result["site"]
        if not result["ok"]:
            st.error(f"{site}: scraping failed — {result['error']}")
            continue
        frame = result["frame"]
        if frame.empty:
            st.warning(
                f"{site}: no menu items found. The site layout may have changed — "
                "the scraper's selectors likely need updating."
            )
            continue
        with st.expander(f"{site} — {len(frame)} items", expanded=len(results) == 1):
            saved_to = result.get("path")
            archived_to = result.get("archived")
            if saved_to:
                caption = f"Saved to `{saved_to}`"
                if archived_to:
                    caption += f" and archived to `{archived_to}`"
                st.caption(caption + ".")
            st.dataframe(frame, use_container_width=True)
            st.download_button(
                label="Download CSV",
                data=dataframe_to_csv_bytes(frame),
                file_name=f"{_slug(site)}_menu.csv",
                mime="text/csv",
                key=f"download_{_slug(site)}",
            )

st.divider()

# --- Build the analytics dataset -------------------------------------------
st.subheader("Build the analytics dataset")
st.markdown(
    "Turn the scraped snapshots into what the **Price Analysis** page reads. "
    "Run these after scraping (or re-scraping) sites:\n\n"
    "1. **Scrape sites** above — each scrape is archived to "
    "`data/scrapes/<date>/` (a dated snapshot of every site).\n"
    "2. **Run enrichment** — classifies dishes in `all_company_prices.csv` that are "
    "not yet in `menu_mappings.db`.\n"
    "3. **Update price history** — stacks the frozen `prior` base with every dated "
    "archive into `all_company_prices.csv` (the dashboard's source of truth).\n"
    "4. Open **Price Analysis** to explore the refreshed data."
)

_snapshot_paths = sorted({str(p) for p in SNAPSHOT_BY_SITE.values()})
_snapshots_present = sum(1 for p in _snapshot_paths if Path(p).exists())
_history_path = history_archive.ALL_COMPANY_PRICES_PATH
_archive_dates = history_archive.list_archive_dates()

status_col1, status_col2, status_col3, status_col4 = st.columns(4)
status_col1.metric("Menu snapshots", f"{_snapshots_present}/{len(_snapshot_paths)}")
status_col2.metric("Dated archives", len(_archive_dates))
status_col3.metric("All company prices", "Ready" if _history_path.exists() else "Missing")
status_col4.metric("Dish mappings", "Ready" if DEFAULT_DB_PATH.exists() else "Missing")


def _has_openai_key() -> bool:
    if os.environ.get("OPENAI_API_KEY"):
        return True
    try:
        return bool(st.secrets.get("OPENAI_API_KEY"))
    except Exception:
        return False


history_col, enrich_col = st.columns(2)

with enrich_col:
    from scripts.enrich_menus import count_pending_dishes, enrich_all

    _counts = count_pending_dishes(db_path=DEFAULT_DB_PATH)
    _pending = _counts["pending"]
    _prices_ready = _history_path.exists()

    if st.button(
        "Run enrichment",
        disabled=not _has_openai_key() or not _prices_ready or _pending == 0,
    ):
        progress = st.progress(0.0, text="Classifying dishes...")

        def _on_progress(done: int, total: int, _result) -> None:
            progress.progress(done / total, text=f"Classifying dishes... {done}/{total}")

        with st.spinner(f"Classifying {_pending} new dish(es) via OpenAI..."):
            summary = enrich_all(on_progress=_on_progress)
        progress.empty()

        if summary["status"] == "empty":
            st.warning(
                "No dishes found in `all_company_prices.csv`. "
                "Click **Update price history** first."
            )
        elif summary["status"] == "up_to_date":
            st.success(f"All {summary['total']} dishes already classified.")
        else:
            st.success(
                f"Classified {summary['succeeded']}/{summary['total']} new dishes "
                f"({summary['failed']} failed). Stored in `menu_mappings.db`."
            )
        st.rerun()

    if not _prices_ready:
        st.caption(
            "Build `all_company_prices.csv` first (step 3 below) before enrichment can run."
        )
    elif _pending == 0 and _counts["total"] > 0:
        st.caption(
            f"All {_counts['total']} dishes in `all_company_prices.csv` are already "
            "mapped in `menu_mappings.db` — nothing new to classify."
        )
    elif _pending:
        st.caption(
            f"{_pending} of {_counts['total']} dishes in `all_company_prices.csv` "
            "are not yet in `menu_mappings.db` (already-mapped dishes are skipped)."
        )
    if not _has_openai_key():
        st.caption(
            "Set `OPENAI_API_KEY` (env var or `st.secrets`) to enable this, or run "
            "`python -m scripts.enrich_menus` locally. Uses the OpenAI API (has a cost)."
        )

with history_col:
    if st.button("Update price history", type="primary", key="update_prices"):
        with st.spinner("Composing all company prices from prior base + dated archives..."):
            st.session_state["prices_update_summary"] = history_archive.update_all_company_prices()

    _prices_summary = st.session_state.get("prices_update_summary")
    if _prices_summary:
        st.success(
            f"Wrote {_prices_summary['rows']} rows across {len(_prices_summary['sources'])} brands "
            f"and {_prices_summary['dates']} dates to `all_company_prices.csv`."
        )
        st.caption(
            f"Sources: {', '.join(_prices_summary['sources']) or '—'} · "
            f"Dated archives: {len(_prices_summary['archives'])} · "
            f"Brands from snapshots: "
            f"{', '.join(_prices_summary.get('brands_from_archives', [])) or '—'}"
        )
    st.caption(
        "Stacks the frozen `prior/price_history.csv` base with every dated archive. "
        "Delete a day below to drop it on the next update."
    )

# --- Manage dated archives -------------------------------------------------
if _archive_dates:
    with st.expander(f"Manage dated archives ({len(_archive_dates)})"):
        st.caption(
            "Each folder is one scrape date under `data/scrapes/`. Deleting a day "
            "removes it from the next **Update price history**."
        )
        for archive_date in reversed(_archive_dates):
            label = archive_date.isoformat()
            row_left, row_right = st.columns([3, 1])
            file_count = len(list(history_archive.archive_dir_for(archive_date).glob("*.csv")))
            row_left.write(f"`{label}` — {file_count} snapshot file(s)")
            if row_right.button("Delete", key=f"del_archive_{label}"):
                history_archive.delete_archive(archive_date)
                st.rerun()
