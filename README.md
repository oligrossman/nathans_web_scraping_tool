# Wagamama Menu Intelligence

A Streamlit platform for Wagamama's competitive pricing team. It scrapes
competitor food-chain menus (item names + prices) using Selenium, previews
results in a table, and lets you download them as CSVs. A dedicated **Price
Analytics** page benchmarks Wagamama against the market with synthetic
historical trends and real collection-vs-delivery comparisons.

The first supported competitor sites are **Nando's** (Camden) and **Pizza
Express** (Birmingham Bullring), each with collection and Deliveroo delivery
scrapers. The architecture is built so additional chains can be added by
writing a scraper class and registering it.

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

Then in the browser, use the sidebar to switch between **Data** (menu scraping)
and **Price Analysis** (analytics dashboard):

1. Check the **Scrape status** table for when each site was last scraped.
2. Select one or more chains (or **Select all sites**).
3. Click **Scrape selected** — the chosen sites are scraped concurrently.
4. Expand each result to review the table and **Download CSV**.

Each successful scrape is cached to `data/scrapes/` and its time recorded in
`data/scrape_log.json`, so you can see what is already fresh before re-running.

## Adding a new chain

1. Create `src/scrapers/<chain>.py` with a class that subclasses `BaseScraper`
   and implements `scrape() -> list[MenuItem]`.
2. Register it in `src/scrapers/registry.py`.

That's it - the UI updates automatically from the registry.

## Price analytics (synthetic)

The **Price Analytics** page (in the sidebar) explores 5 years of *synthetic*
quarterly price history for:

- **Wagamama** — our brand, with a distinct bespoke menu.
- **Nando's / Pizza Express** — competitor companies anchored on live scraped menus.
- **Market** — derived at load time as a restaurant-count-weighted average of
  competitor companies (weights in `data/company_weights.csv`).

All menu categories collapse into five board-level buckets (Starters, Mains,
Sides, Drinks, Desserts), plus a **Total** series (equal-weighted mean across
buckets). The page shows five views:

- **By bucket** — bucket breakdown (absolute, indexed, heatmap with Total row).
- **Wagamama vs market** — breakdown by bucket (including **Total** as a facet
  panel alongside Starters, Mains, etc.), focus chart, and latest-price bars.
  Pick which market benchmark(s) to compare against — **Market whole** (a
  restaurant-count-weighted average of all selected competitors) and any custom
  markets. All market benchmarks exclude Wagamama dishes.
- **Collection vs delivery (real data)** — click-through drilldown with
  **← Back**, breadcrumb, or chart click: Company → Bucket → Item.
- **High vs low** — lowest, average and highest menu prices with the same
  drilldown pattern (item level shows each price within its bucket range).

The **Comparison Configuration** page holds the settings that shape the
analytics charts: LLM-generated dish attributes (cuisine, dish type such as
Noodles / Pizza / Curry / Soup, and dietary flags) stored in SQLite with user
overrides, the grouping dimension (course, cuisine, dish type), and custom
market benchmarks (e.g. Asian noodle dishes, all pizzas).

  Each dish is keyed by a **`dish_id`** unique per `(restaurant, item)` — so the
  same name at two chains (e.g. "Mac & Cheese") maps and overrides independently.
  The id is deterministic (`src/enrichment/identity.py`) and materialised into
  both `data/price_history.csv` and `data/menu_mappings.db`, which is how
  mappings join back to each restaurant and its price history.

  Offline enrichment (API key never exposed in the app):

  ```bash
  export OPENAI_API_KEY=sk-...
  python -m scripts.enrich_menus                  # classify new dishes only
  python -m scripts.enrich_menus --limit 10       # cost control while testing
  python -m scripts.enrich_menus --concurrency 16 # parallel async calls
  python -m scripts.enrich_menus --remap          # reclassify everything
  ```

  Optional: `OPENAI_MODEL` (default `gpt-5-nano`). Mappings persist in
  `data/menu_mappings.db`.

  Upgrading an older, name-keyed database to the per-restaurant `dish_id`
  schema (preserves existing classifications, no re-spend):

  ```bash
  python -m scripts.generate_data      # rewrite price_history.csv with dish_id
  python -m scripts.migrate_dish_ids   # backfill mappings into the new schema
  ```

Charts use Wagamama brand red (`#D11317`) for our brand and Deliveroo teal
(`#00CCBC`) for delivery channel comparisons.

### Generating / refreshing the data

The datasets in `data/` are committed so the page works out of the box. To
regenerate them (deterministic - uses a fixed random seed):

```bash
python -m scripts.generate_data            # reuse the cached menu snapshot
python -m scripts.generate_data --rescrape # force a fresh scrape first
```

This writes `data/nandos_menu_snapshot.csv`, `data/pizza_express_menu_snapshot.csv`,
`data/deliveroo_nandos_snapshot.csv`, `data/deliveroo_pizza_express_snapshot.csv`,
`data/price_history.csv` (Nando's + Pizza Express + Wagamama), and reads
`data/company_weights.csv` for the Market benchmark weights.

Brand constants (colours, copy, own-brand label) live in `src/branding.py`.

## A note on respectful scraping

This prototype scrapes only when you click the button. Please use it
considerately: avoid hammering sites with rapid repeated requests. Automated,
timed scraping is intentionally **out of scope** for this proof of concept.
