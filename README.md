# 🛒 Mercado Libre Scraper (Scrapfly, MySQL-driven)

Scrapes product data from **Mercado Libre Argentina** product URLs using the
[Scrapfly SDK](https://scrapfly.io/docs/python-sdk) and stores everything in
**MySQL** — no JSON files on disk:

- **URLs in**: read from a source table (`scraping_urls` by default)
- **Results out**: one row per URL in a results table with a
  **`data_scrapped` JSON column** (`scraping_results` by default)
- **Fields config**: which fields to extract and *how* (CSS selectors,
  attributes, regex, required/optional) lives in a config table
  (`scraping_field_config` by default) — add or remove a field by adding or
  removing a row, no code changes.

## What is scraped

Nothing is hardcoded: the fields come from the `scraping_field_config`
table (or the built-in defaults in `app/services/parser.py`). Defaults:

| Field                    | Source (CSS selector, in order)                        |
| ------------------------ | ------------------------------------------------------ |
| `title`                  | `h1.ui-pdp-title` (required — page fails if missing)   |
| `price`                  | `.ui-pdp-price__second-line ...` → `.ui-pdp-price__part ...` → any `andes-money-amount__fraction` |
| `competitor` (seller)    | `h2.ui-seller-data-header__title` → fallback "Vendido por" regex |
| `price_in_installments`  | `.ui-pdp-price__subtitles` → buy-box "cuotas" regex    |
| `image`                  | `img.ui-pdp-image` (`src` attribute)                   |

Statuses per URL: `successed`, `discarded` (product unavailable / 404),
`failed` (blocked or unparseable after all tiers).

## How the engine works

1. **Concurrency is adaptive** — at startup the engine asks the Scrapfly
   [Account API](https://scrapfly.io/docs/account) for
   `subscription.usage.scrape.concurrent_limit` and uses
   `min(SCRAPFLY_MAX_CONCURRENCY, account_limit)` workers.
2. **Tiered waves** (verified against real catalog pages):
   - Wave A, tier 0: residential + ASP + JS render (`rendering_wait` 6 s) → ~30 credits / ~15-25 s
   - Wave A, tier 1: same with 8 s wait, fresh session (fresh browser + IP)
   - Wave B (rescue): same with 10 s wait, low concurrency — only for stubborn URLs
   - No `auto_scroll` (it triggers ML's login wall) and no `wait_for_selector`.
3. **Sessions** — one sticky session per worker, so the headless browser
   stays warm across URLs (measured ~25% faster after warm-up); every
   retry uses a brand-new session/IP.
4. Per-URL records keep `_attempts` (stage, error, cost, duration) and
   `_api_cost_total` (sum across attempts).

> Mercado Libre login-walls plain-HTTP clients ("¡Hola! Para continuar,
> ingresa a tu cuenta"), including datacenter proxies — so every tier uses
> residential + JS. The old code did the same 30-credit render per URL but
> at concurrency 5 with 1.5-3.5 s sleeps per URL and a *sequential* retry
> pass, which is why ~900 URLs took 40–60 min.
>
> Note: on catalog pages (`/p/MLA...`) ML hides the seller block and the
> installments/payment summary in the server-rendered HTML, so
> `competitor` and `price_in_installments` are legitimately `n/a` there.

## Run

### Production (legacy mode, default)

`SCRAPE_RESULTS_MODE=legacy` (the default):

1. **URLs in**: `scrapped_competence.catalog_link`
2. **Field config**: `scraping_field_config` (single source of truth)
3. **Results out**: `UPDATE scrapped_competence` (flat columns:
   `title, price, competitor, price_in_installments, image, timestamp,
   status, api_cost_total, remaining_credits`)
4. **Google Sheet**: after the DB write, the "Catalogo" tab is updated
   (columns A..J = url, title, price, competitor, price_in_installments,
   image, timestamp, status, api_cost_total, remaining_credits). Rows are
   matched by the URL in column A and updated in place; unknown URLs are
   appended. This is a legacy process that will be dropped.

Google Sheet auth: `SERVICE_ACCOUNT_FILE=/path/service_account.json` in
`.env`, and the service account email must be shared as **Editor** on the
spreadsheet. Without credentials the sheet step is skipped (logged).

```bash
python run_scraper.py
```

### Future (json mode — NOT IN USE YET)

`SCRAPE_RESULTS_MODE=json` writes to `scraping_results`
(`data_scrapped` JSON column) instead. The code is in place but **disabled
until the migration** — do not switch yet.

### Without a database (local smoke test)

`python run_scraper.py urls.txt` reads the file instead and only prints the
summary (results are not persisted). `--dump out.json` optionally saves a
copy for debugging.

### Options

```bash
python run_scraper.py [urls.txt] [--concurrency N] [--no-js-rescue]
                      [--dump debug.json] [--dry-run]
```

## Adding / removing a scraped field

Insert (or disable) a row in `scraping_field_config`:

```sql
-- stop scraping the image
UPDATE scraping_field_config SET enabled = 0 WHERE field_name = 'image';

-- scrape a new field, e.g. the product's subtitle
INSERT INTO scraping_field_config
  (field_name, kind, selectors, default_value, required, priority)
VALUES ('subtitle', 'field', JSON_ARRAY('.ui-pdp-subtitle'), 'n/a', 0, 60);
```

Available knobs per row: `selectors` (ordered list), `attribute`, `regex`,
`regex_on_html`, `exclude_class_contains`, `exclude_ancestor_class`,
`default_value`, `required`, plus `discard_phrase` / `block_phrase` /
`block_class` rows to tune the page classification.

## Production flow (webhook → Cloud Run)

`POST /webhooks/start_scrapping` with JSON `{"secret": "<SECRET_GUIAS>"}`
dispatches the pipeline in a background thread and returns `202`:

1. Load URLs from `scrapped_competence` (legacy mode).
2. Load field rules from `scraping_field_config` (fails loudly if missing).
3. Scrape with the tiered engine.
4. `UPDATE scrapped_competence` (flat columns, keyed by catalog_link).
5. Update the Google Sheet "Catalogo" tab (legacy, best-effort).
6. WhatsApp notification (Whapi) with remaining Scrapfly credits.

## Project structure

```
main.py                        Flask app (Cloud Run entrypoint, gunicorn)
run_scraper.py                 standalone CLI runner (DB or txt file)
sql/schema.sql                 placeholder DDL (json-mode tables + field config)
sql/field_config.sql           scraping_field_config DDL + default field seeds
app/
  services/
    scraper.py                 async engine: waves, sessions, retry ladder
    parser.py                  rules-driven HTML extraction + classification
    rules.py                   loads field rules (DB config table -> rules)
    pipeline_scrapping.py      orchestration (webhook entrypoint)
    url_source.py              URL loading (DB legacy/json tables or txt file)
    sheets.py                  legacy Google Sheet "Catalogo" updater
    budget.py                  remaining-credits query
    notification.py            Whapi WhatsApp notifications
    webhook.py                 Flask blueprint
  database/db_manager.py       MySQL/Cloud SQL: legacy + json tables, field config
  settings/config.py           env vars + tier ladder + tunables
  utils/logger.py
tests/                         parser fixtures, mocked engine, MariaDB integration
```

## Tuning knobs (`.env`)

| Variable                    | Default | Meaning                                   |
| --------------------------- | ------- | ----------------------------------------- |
| `SCRAPFLY_MAX_CONCURRENCY`  | `30`    | hard cap on concurrent requests           |
| `SCRAPFLY_JS_CONCURRENCY`   | `8`     | concurrency of the deep JS rescue wave    |
| `SCRAPE_RESULTS_MODE`       | `legacy` | `legacy` = scrapped_competence + Sheet; `json` = scraping_results (not in use yet) |
| `LEGACY_TABLE`              | `scrapped_competence` | legacy flat table (URLs in + results out) |
| `GOOGLE_SHEET_URL` / `GOOGLE_SHEET_TAB` | Catalogo sheet | legacy sheet updated after the DB write |
| `MYSQL_HOST` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_NAME` | — | plain MySQL connection |
| `SERVICE_ACCOUNT_FILE`      | — | GCP service account (Cloud SQL auth + Sheets auth) |
| `SCRAPE_URLS_TABLE` / `SCRAPE_URL_COLUMN` | `scraping_urls` / `url` | json-mode source table |
| `SCRAPE_RESULTS_TABLE`      | `scraping_results` | json-mode results table |
| `SCRAPE_FIELDS_TABLE`       | `scraping_field_config` | field-extraction config table |
| `URLS_FILE`                 | `urls.txt` | URL file used when no DB is configured |

## Tests

```bash
python -m pytest tests/          # DB tests auto-skip without a local MariaDB
```
