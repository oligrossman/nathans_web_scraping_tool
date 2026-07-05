"""Data collection page — scrape competitor menus and download CSVs."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import streamlit as st

from src import auth, scrape_log
from src.branding import APP_TAGLINE, APP_TITLE, SCRAPE_SECTION, apply_sidebar_branding, brand_css
from src.export import dataframe_to_csv_bytes, items_to_dataframe
from src.scrapers.registry import SCRAPERS, get_scraper

SCRAPES_DIR = Path(__file__).resolve().parent.parent / "data" / "scrapes"
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
    """Save each successful scrape to disk and record its scrape time."""
    SCRAPES_DIR.mkdir(parents=True, exist_ok=True)
    for result in results:
        if not result["ok"]:
            continue
        result["frame"].to_csv(SCRAPES_DIR / f"{_slug(result['site'])}.csv", index=False)
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
            st.dataframe(frame, use_container_width=True)
            st.download_button(
                label="Download CSV",
                data=dataframe_to_csv_bytes(frame),
                file_name=f"{_slug(site)}_menu.csv",
                mime="text/csv",
                key=f"download_{_slug(site)}",
            )
