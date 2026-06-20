"""Persistent log of when each site was last scraped.

A tiny JSON store (``data/scrape_log.json``) records the last scrape time and
item count per site. The home page reads this so the user can see what is
already fresh and avoid re-scraping (and wasting compute) unnecessarily.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pandas as pd

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"
LOG_PATH = _DATA_DIR / "scrape_log.json"


def load_log() -> dict[str, dict]:
    """Return the raw log mapping ``site_name -> {last_scraped, item_count}``."""
    if not LOG_PATH.exists():
        return {}
    try:
        return json.loads(LOG_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def record_scrape(
    site_name: str, item_count: int, *, when: datetime | None = None
) -> None:
    """Persist the latest scrape time and item count for ``site_name``."""
    log = load_log()
    timestamp = (when or datetime.now()).astimezone().isoformat(timespec="seconds")
    log[site_name] = {"last_scraped": timestamp, "item_count": item_count}
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(log, indent=2, sort_keys=True))


def last_scraped(site_name: str) -> str | None:
    """Return the ISO timestamp of the last scrape, or ``None`` if never."""
    return load_log().get(site_name, {}).get("last_scraped")


def _parse(timestamp: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(timestamp) if timestamp else None
    except (TypeError, ValueError):
        return None


def _format_timestamp(timestamp: str | None) -> str:
    parsed = _parse(timestamp)
    return parsed.strftime("%Y-%m-%d %H:%M") if parsed else "Never"


def _humanise_age(timestamp: str | None) -> str:
    parsed = _parse(timestamp)
    if parsed is None:
        return "Never"
    now = datetime.now(parsed.tzinfo) if parsed.tzinfo else datetime.now()
    seconds = (now - parsed).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"


def status_table(site_names: list[str]) -> pd.DataFrame:
    """Build a display table of scrape status for the given sites."""
    log = load_log()
    rows = [
        {
            "Site": name,
            "Last scraped": _format_timestamp(log.get(name, {}).get("last_scraped")),
            "Age": _humanise_age(log.get(name, {}).get("last_scraped")),
            "Items": log.get(name, {}).get("item_count", "—"),
        }
        for name in site_names
    ]
    return pd.DataFrame(rows)
