"""Reload project modules inside a long-running Streamlit process.

Streamlit reads a page again on each rerun, but Python keeps the first import
of every ``src`` module. A scraper or a compiler added after the app started
would stay invisible until the process was killed. The Data page reloads these
modules before it builds the scrape list or recompiles price history.
"""

from __future__ import annotations

import importlib
import types


def reload_branding() -> types.ModuleType:
    """Reload UI copy so a label added after startup is on the next rerun."""
    import src.branding as branding

    return importlib.reload(branding)


def reload_scrape_registry() -> types.ModuleType:
    """Reload restaurant config and the scraper registry, then return the registry."""
    import src.restaurants.config as config
    import src.scrapers.registry as registry

    importlib.reload(config)
    return importlib.reload(registry)


def reload_history_compiler() -> types.ModuleType:
    """Reload the tracker matcher and the history archive, then return the archive."""
    import src.history_archive as history_archive
    import src.pricing_tracker as pricing_tracker

    importlib.reload(pricing_tracker)
    return importlib.reload(history_archive)
