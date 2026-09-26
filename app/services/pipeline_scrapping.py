"""
End-to-end scraping pipeline (DB-first, no JSON files on disk).

1. Load URLs from MySQL (legacy mode: scrapped_competence.catalog_link;
   json mode: scraping_urls; file fallback only when no DB is configured).
2. Load field-extraction rules from the scraping_field_config table
   (single source of truth when a DB is configured).
3. Scrape everything with the fast tiered engine.
4. Persist results:
     legacy mode (default) -> UPDATE scrapped_competence (flat columns)
                              + Google Sheet "Catalogo" (legacy, best-effort)
     json mode             -> upsert scraping_results.data_scrapped (JSON)
                              *** NOT IN USE YET - kept for the migration ***
5. WhatsApp notification with the remaining Scrapfly budget.
"""
import asyncio

from app.database import db_manager
from app.settings import config as cfg
from app.services import sheets
from app.services.budget import remain_budget
from app.services.notification import enviar_mensaje_whapi
from app.services.rules import load_rules
from app.services.scraper import ScrapeEngine
from app.services.url_source import load_urls
from app.utils.logger import logger


def run_pipeline(urls=None, skip_js_rescue=False,
                 max_concurrency=None, js_concurrency=None, limit=None):
    """
    Returns (records, stats). When a database is configured, results are
    persisted according to SCRAPE_RESULTS_MODE; nothing is written to disk.
    `limit` caps the number of URLs scraped (useful for test runs).
    """
    if urls is None:
        urls = load_urls()
    if limit and limit > 0:
        urls = urls[:limit]
    if not urls:
        logger.info("No URLs to scrape - nothing to do.")
        return [], {"total": 0}

    rules = load_rules()
    engine = ScrapeEngine(rules=rules, max_concurrency=max_concurrency,
                          js_concurrency=js_concurrency)
    records, stats = asyncio.run(engine.run(urls, skip_js_rescue=skip_js_rescue))

    if not db_manager.is_db_configured():
        logger.warning("No database configured - results were NOT persisted anywhere.")
        return records, stats

    budget_message, remaining_credits = remain_budget()

    if cfg.SCRAPE_RESULTS_MODE == "legacy":
        # ── old behavior: flat UPDATE of scrapped_competence + sheet ──
        rows = db_manager.normalize_legacy_rows(records, remaining_credits)
        db_manager.update_legacy_rows(rows)
        sheets.update_catalogo(rows)
        logger.info("Pipeline finished (legacy mode: flat DB update + Google Sheet).")
        return records, stats

    # ── default mode: JSON results table + Google Sheet ──
    for record in records:
        record["remaining_credits"] = remaining_credits
    db_manager.upsert_results(records)
    rows = db_manager.normalize_legacy_rows(records, remaining_credits)
    sheets.update_catalogo(rows)
    logger.info("Pipeline finished (json mode: scraping_results + Google Sheet).")
    return records, stats


def scrapping():
    """Entrypoint used by the webhook (and historically by the scheduler)."""
    try:
        enviar_mensaje_whapi("comenzando scrapping")
        records, stats = run_pipeline()
        budget_data, _credits = remain_budget()
        enviar_mensaje_whapi(budget_data)
        logger.info("Pipeline finished.")
    except Exception:
        logger.exception("Pipeline crashed - the background job did not complete.")
