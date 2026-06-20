# Nathan's Menu Scraper

A Streamlit prototype that scrapes a food chain's online menu (item names + prices)
using Selenium, previews the result in a table, and lets you download it as a CSV.

The first supported site is **Nando's** (UK collection menu). The architecture is
built so additional chains (e.g. Wagamama) can be added by writing a single new
scraper class.

## How it works

```
app.py (Streamlit UI)
  -> registry.py        picks the right scraper for the chosen chain
    -> NandosScraper    drives headless Chrome, waits for the menu to render,
                        scrolls to load everything, extracts name + price
      -> MenuItem[]     a typed list of menu items
  -> export.py          turns MenuItem[] into a CSV you can download
```

## Requirements

- Python 3.10+
- Google Chrome installed locally (Selenium 4's built-in Selenium Manager
  resolves the matching driver automatically; no manual chromedriver setup).

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Run

```bash
streamlit run app.py
```

Then in the browser:

1. Pick a chain from the dropdown.
2. Click **Scrape menu**.
3. Review the table and click **Download CSV**.

## Adding a new chain

1. Create `src/scrapers/<chain>.py` with a class that subclasses `BaseScraper`
   and implements `scrape() -> list[MenuItem]`.
2. Register it in `src/scrapers/registry.py`.

That's it - the UI updates automatically from the registry.

## Price analytics (synthetic)

The **Price Analytics** page (in the sidebar) explores 5 years of *synthetic*
quarterly price history for:

- **Nando's** - anchored on the live scraped menu, back-cast over time.
- **WagaNath** - Nathan's own pan-Asian restaurant (a distinct bespoke menu).
- **Market** - derived at load time as a restaurant-count-weighted average of
  competitor companies (weights in `data/company_weights.csv`).

All 11 Nando's categories collapse into five board-level buckets (Starters,
Mains, Sides, Drinks, Desserts), plus a **Total** series (equal-weighted mean
across buckets). The page shows four views:

- **By bucket** — bucket breakdown (absolute, indexed, heatmap with Total row).
- **WagaNath vs market** — breakdown by bucket (including **Total** as a facet
  panel alongside Starters, Mains, etc.), focus chart, and latest-price bars.
- **Collection vs delivery (real data)** — click-through drilldown with
  **← Back**, breadcrumb, dropdown, or chart click: Company → Bucket → Item.
- **High vs low** — lowest, average and highest menu prices with the same
  drilldown pattern (item level shows each price within its bucket range).

### Generating / refreshing the data

The datasets in `data/` are committed so the page works out of the box. To
regenerate them (deterministic - uses a fixed random seed):

```bash
python -m scripts.generate_data            # reuse the cached menu snapshot
python -m scripts.generate_data --rescrape # force a fresh Nando's scrape first
```

This writes `data/nandos_menu_snapshot.csv` (anchor prices),
`data/price_history.csv` (Nando's + WagaNath; Market is derived at load time),
`data/deliveroo_nandos_snapshot.csv` (Deliveroo delivery prices), and reads
`data/company_weights.csv` for the Market benchmark weights.

## A note on respectful scraping

This prototype scrapes only when you click the button. Please use it
considerately: avoid hammering the site with rapid repeated requests. Automated,
timed scraping is intentionally **out of scope** for this proof of concept.
