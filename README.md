# Wagamama Menu Intelligence

A Streamlit platform for Wagamama's competitive pricing team. It scrapes
competitor food-chain menus (item names + prices) using Selenium, previews
results in a table, and lets you download them as CSVs. A dedicated **Price
Analytics** page benchmarks Wagamama against the market using observed prices:
the pricing tracker, plus dated restaurant-menu scrapes.

The first supported competitor sites are **Nando's** (Camden) and **Pizza
Express** (Birmingham Bullring), each with restaurant-website and Deliveroo delivery
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
- On Streamlit Community Cloud, `packages.txt` installs Debian's `chromium`
  and `chromium-driver`. Without those packages the downloaded chromedriver
  exits with status 127 and every scrape fails. Reboot the app after adding
  the file so apt installs them.

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

Each successful scrape writes its canonical snapshot in `data/`
(`<slug>_menu_snapshot.csv` for restaurant menus, `<platform>_<slug>_snapshot.csv`
for delivery) — the *latest* menu used by the delivery/high-low comparisons — and
also files a dated copy under `data/scrapes/<YYYY-MM-DD>/`. Scrape times are
recorded in `data/scrape_log.json`.

Then, in the **Build the analytics dataset** section on the same page:

5. **Run enrichment** — classifies dishes in `data/all_company_prices.csv` that are
   not yet in `data/menu_mappings.db` (needs `OPENAI_API_KEY`; only unmapped
   dishes are sent, so there's no duplicate cost).
6. **Update price history** — stacks the frozen `prior` base with every dated
   archive into `data/all_company_prices.csv` (the dashboard's source of truth).
7. Open **Price Analysis** to explore the refreshed data.

### How price history accumulates

`data/all_company_prices.csv` is composed from two observed sources:

- `data/scrapes/prior/price_history.csv` — the frozen base imported from
  `Master - Pricing Tracker.xlsx`. Seeded automatically the first time from
  whatever `all_company_prices.csv` already exists.
- `data/scrapes/<YYYY-MM-DD>/*.csv` — one dated snapshot per scrape. Each
  restaurant menu becomes one observation (per item, per date) when you click
  **Update price history**.

Delete a dated folder (there's a **Manage dated archives** panel on the Data page)
and the next update drops those observations — an easy way to prune bad scrapes.
Only restaurant menus feed history. Delivery snapshots stay in the archive for
the restaurant-vs-delivery comparison.

## Adding a new chain

1. Create `src/scrapers/<chain>.py` with a class that subclasses `BaseScraper`
   and implements `scrape() -> list[MenuItem]`.
2. Register it in `src/scrapers/registry.py`.

That's it - the UI updates automatically from the registry.

## Price analytics

The **Price Analytics** page (in the sidebar) explores observed price history for:

- **Wagamama** — our brand. History comes from the pricing tracker and the
  dated restaurant-menu scrapes.
- **Nando's / Pizza Express** and the other scraped chains — the same two
  observed sources.
- **Market** — derived at load time as a restaurant-count-weighted average of
  competitor companies (weights in `data/company_weights.csv`). Every market
  point is an average of those observed competitor prices.

All menu categories collapse into six board-level buckets (Starters, Mains,
Sides, Drinks (non-alcoholic), Drinks (alcoholic), Desserts), plus a **Total**
series (equal-weighted mean across buckets). The page shows five views:

- **By bucket** — bucket breakdown (absolute, indexed, heatmap with Total row).
- **Wagamama vs market** — breakdown by bucket (including **Total** as a facet
  panel alongside Starters, Mains, etc.), focus chart, and latest-price bars.
  Pick which market benchmark(s) to compare against — **Market whole** (a
  restaurant-count-weighted average of all selected competitors) and any custom
  markets. All market benchmarks exclude Wagamama dishes.
- **Restaurant vs delivery** — latest scraped menu against the latest delivery
  menu. The left chart compares restaurants. Choosing a bucket keeps that
  comparison and lists the dishes on the right.
- **High vs low** — lowest, average and highest price on each restaurant's
  latest menu (not an average over the price history), with the same
  left-hand restaurant comparison.

The **Comparison Configuration** page holds the settings that shape the
analytics charts: LLM-generated dish attributes (cuisine, dish type such as
Noodles / Pizza / Curry / Soup, and dietary flags) stored in SQLite with user
overrides, the grouping dimension (course, cuisine, dish type), and custom
market benchmarks (e.g. Asian noodle dishes, all pizzas).

  Each dish is keyed by a **`dish_id`** unique per `(restaurant, item)` — so the
  same name at two chains (e.g. "Mac & Cheese") maps and overrides independently.
  The id is deterministic (`src/enrichment/identity.py`) and materialised into
  both `data/all_company_prices.csv` and `data/menu_mappings.db`, which is how
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
  python -m scripts.generate_data      # rewrite all_company_prices.csv with dish_id
  python -m scripts.migrate_dish_ids   # backfill mappings into the new schema
  ```

Charts use Wagamama brand red (`#D11317`) for our brand and Deliveroo teal
(`#00CCBC`) for delivery channel comparisons.

### Generating / refreshing the data

The datasets in `data/` are committed so the page works out of the box. You can
rebuild them from the **Data** page buttons, or from the CLI:

```bash
python -m scripts.scrape_all               # scrape every chain into snapshots
python -m scripts.generate_data            # scrape, then recompose observed history
python -m scripts.generate_data --rescrape # force a fresh scrape first
python -m scripts.generate_data --skip-scrape # recompose history from snapshots only
```

`generate_data` recomposes `data/all_company_prices.csv` from the pricing
tracker and the dated restaurant archives. It does not estimate a price. The
Market benchmark weights are in `data/company_weights.csv`. **Update price
history** on the Data page runs the same recompose.

Brand constants (colours, copy, own-brand label) live in `src/branding.py`.

## Read-only MCP server

A separate process exposes the same analytics as the Streamlit dashboard over
MCP (Model Context Protocol), so Claude Desktop, Codex, or Cursor can ask
pricing questions without cloning the repo. Streamlit Cloud does **not** host
this — keep installing `requirements.txt` for the dashboard; use
`requirements-mcp.txt` only on the MCP host.

### Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-mcp.txt
export MCP_AUTH_TOKEN='choose-a-long-random-token'
python -m mcp_server
```

Defaults: `http://127.0.0.1:8000/mcp` (override with `MCP_HOST` / `MCP_PORT`).
`GET /health` is open; every other path requires `Authorization: Bearer <token>`.

### Connect a client

Paste the server URL and bearer token into the app’s MCP / connector settings
(no project checkout needed for directors):

| Client | Where |
| --- | --- |
| **Claude Desktop** / **Codex** | MCP connector settings: URL + `Authorization: Bearer <token>` |
| **Cursor** | MCP settings: same URL + bearer header |

**Claude.ai** (browser) expects OAuth for custom connectors, not a pasted bearer
token — v1 does not cover that. Use Claude Desktop, or add OAuth later.

Hosting (Render, Fly, a VM) needs this repo’s `data/` directory on disk. Scrapes
that exist only on Streamlit Cloud are invisible here until those files are
copied onto the MCP host.

## A note on respectful scraping

This prototype scrapes only when you click the button. Please use it
considerately: avoid hammering sites with rapid repeated requests. Automated,
timed scraping is intentionally **out of scope** for this proof of concept.
