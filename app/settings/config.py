import os
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────────────────────
# SCRAPFLY
# ──────────────────────────────────────────────────────────────
SCRAP_KEY = os.getenv("SCRAPFLY_API_KEY")
SCRAPFLY_API_KEY = SCRAP_KEY  # alias

# ──────────────────────────────────────────────────────────────
# DATABASE (MySQL)
#
# Two ways to connect:
#   1) Plain MySQL  -> set MYSQL_HOST / MYSQL_USER / MYSQL_PASSWORD (+ optional port/name)
#   2) Google Cloud SQL -> set INSTANCE_DB / USER_DB / PASSWORD_DB / NAME_DB (legacy)
# Table names are placeholders until the real schema is designed.
# ──────────────────────────────────────────────────────────────
MYSQL_HOST = os.getenv("MYSQL_HOST")
MYSQL_PORT = int(os.getenv("MYSQL_PORT") or 3306)
MYSQL_USER = os.getenv("MYSQL_USER")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD")
MYSQL_NAME = os.getenv("MYSQL_NAME")

INSTANCE_DB = os.getenv("INSTANCE_DB")
USER_DB = os.getenv("USER_DB")
PASSWORD_DB = os.getenv("PASSWORD_DB")
NAME_DB = os.getenv("NAME_DB")
MELI_SCHMA = os.getenv("MELI_SCHMA")

# Path to a Google service-account JSON used to authenticate against
# Cloud SQL (sets GOOGLE_APPLICATION_CREDENTIALS).
SERVICE_ACCOUNT_FILE = os.getenv("SERVICE_ACCOUNT_FILE")

# Results persistence mode:
#   "json"    (default) -> URLs from scrapped_competence, results upserted
#                into scraping_results (data_scrapped JSON column)
#                + Google Sheet "Catalogo" (flat A..K values)
#   "legacy"  -> old behavior: flat UPDATE of scrapped_competence + sheet
SCRAPE_RESULTS_MODE = os.getenv("SCRAPE_RESULTS_MODE", "json")

# Legacy flat table (URLs source AND results target in legacy mode).
LEGACY_TABLE = os.getenv("LEGACY_TABLE", "scrapped_competence")

# Google Sheet updated after the DB write (legacy process, to be dropped).
GOOGLE_SHEET_URL = os.getenv(
    "GOOGLE_SHEET_URL",
    "https://docs.google.com/spreadsheets/d/11EF4fqrGlRzbkYBn8v0wxjUbV48ZTfVWfPKQJZDuFok/edit",
)
GOOGLE_SHEET_TAB = os.getenv("GOOGLE_SHEET_TAB", "Catalogo")

# URL source table (json mode).
SCRAPE_URLS_TABLE = os.getenv("SCRAPE_URLS_TABLE", "scraping_urls")
SCRAPE_URL_COLUMN = os.getenv("SCRAPE_URL_COLUMN", "url")

# Results table (json mode; id, url, status, data_scrapped JSON, created_at, updated_at).
SCRAPE_RESULTS_TABLE = os.getenv("SCRAPE_RESULTS_TABLE", "scraping_results")

# Field-extraction config table.
SCRAPE_FIELDS_TABLE = os.getenv("SCRAPE_FIELDS_TABLE", "scraping_field_config")

# ──────────────────────────────────────────────────────────────
# NOTIFICATIONS (WhatsApp via Whapi) — optional
# ──────────────────────────────────────────────────────────────
TOKEN_WHAPI = os.getenv("TOKEN_WHAPI")
PHONE = os.getenv("PHONE")

# ──────────────────────────────────────────────────────────────
# WEBHOOK
# ──────────────────────────────────────────────────────────────
SECRET_GUIAS = os.getenv("SECRET_GUIAS")

# ──────────────────────────────────────────────────────────────
# SPEED / COST TUNING
# ──────────────────────────────────────────────────────────────
# Hard cap on concurrent scrape requests. The engine also asks the
# Scrapfly account API for the plan's concurrent_limit and uses the
# smaller of the two, so this is only an upper bound.
MAX_CONCURRENCY = int(os.getenv("SCRAPFLY_MAX_CONCURRENCY", "30"))

# Concurrency used when the account limit cannot be determined.
FALLBACK_CONCURRENCY = int(os.getenv("SCRAPFLY_FALLBACK_CONCURRENCY", "20"))

# Concurrency for the heavy JS-rendering rescue wave (browser renders
# are slower and more expensive, so keep this modest).
JS_CONCURRENCY = int(os.getenv("SCRAPFLY_JS_CONCURRENCY", "8"))

# URL file used when the database is not configured (see run_scraper.py).
URLS_FILE = os.getenv("URLS_FILE", "urls.txt")

# ──────────────────────────────────────────────────────────────
# RETRY TIER LADDER
#
# Mercado Libre serves a login wall ("¡Hola! Para continuar, ingresa a
# tu cuenta") to plain HTTP clients — including datacenter proxies. The
# verified working combination (measured against real catalog pages) is:
#   residential proxy + ASP + JS rendering, rendering_wait ~6s, NO
#   auto_scroll (auto_scroll triggers the wall), NO wait_for_selector.
#
# Sessions are reused per worker so the headless browser stays warm;
# every retry uses a FRESH session (fresh browser + IP).
# Cost: residential (25) + JS (5) = 30 credits per render.
# ──────────────────────────────────────────────────────────────
BASE_TIER = dict(
    asp=True,                    # anti-bot protection (Mercado Libre needs it)
    render_js=True,              # required: ML walls plain-HTTP clients
    proxy_pool="public_residential_pool",
    country="ar",
    lang=["es-AR", "es"],
    retry=False,                 # we manage retries ourselves (SDK retry adds hidden delays)
    session_sticky_proxy=True,
    raise_on_upstream_error=False,
)

TIER_0_JS = {
    "name": "tier0_res_js",
    "rendering_wait": 6_000,     # ms to wait before capturing the DOM
    "timeout": 60_000,
    "cost_budget": 45,
}

TIER_1_JS = {
    "name": "tier1_res_js_retry",
    "rendering_wait": 8_000,     # stubborn page: give the JS more time
    "timeout": 60_000,
    "cost_budget": 45,
}

TIER_2_JS = {
    "name": "tier2_res_js_deep",
    "rendering_wait": 10_000,    # last chance
    "timeout": 90_000,
    "cost_budget": 50,
}
