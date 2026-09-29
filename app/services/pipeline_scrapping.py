"""
End-to-end scraping pipeline (DB-first, no JSON files on disk).

1. Load URLs from MySQL (always from scrapped_competence.catalog_link;
   file fallback only for local dev without a DB).
2. Load field-extraction rules from the scraping_field_config table
   (single source of truth when a DB is configured).
3. Scrape everything with the fast tiered engine.
4. Persist results:
     json mode (default) -> TWO STAGES:
        stage 1: fast pass (tier0 + tier1) -> upsert scraping_results
                 (JSON column) + Google Sheet "Catalogo" IMMEDIATELY
        stage 2: slow rescue pass (deep JS tiers, fresh sessions) for the
                 failed URLs -> upsert/sheet update those rows again
     legacy mode       -> flat UPDATE of scrapped_competence + sheet
5. WhatsApp notification with the remaining Scrapfly budget.
"""
import asyncio
import time

from app.database import db_manager
from app.settings import config as cfg
from app.services import scraper, sheets
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

    if not db_manager.is_db_configured():
        # local smoke tests without a DB: scrape + report only
        records, stats = asyncio.run(engine.run(urls, skip_js_rescue=skip_js_rescue))
        logger.warning("No database configured - results were NOT persisted anywhere.")
        return records, stats

    budget_message, remaining_credits = remain_budget()

    if cfg.SCRAPE_RESULTS_MODE == "legacy":
        # ── old behavior: flat UPDATE of scrapped_competence + sheet ──
        records, stats = asyncio.run(engine.run(urls, skip_js_rescue=skip_js_rescue))
        rows = db_manager.normalize_legacy_rows(records, remaining_credits)
        db_manager.update_legacy_rows(rows)
        sheets.update_catalogo(rows)
        logger.info("Pipeline finished (legacy mode: flat DB update + Google Sheet).")
        return records, stats

    # ── default (json) mode: two stages ──
    # Stage 1: persist the first pass IMMEDIATELY (failed rows included,
    # so the data is visible right away), then retry failures slowly.
    started = time.time()

    def _persist(recs):
        for record in recs:
            record["remaining_credits"] = remaining_credits
        db_manager.upsert_results(recs)
        rows = db_manager.normalize_legacy_rows(recs, remaining_credits)
        sheets.update_catalogo(rows)

    records, stats_first = asyncio.run(engine.run(urls, skip_js_rescue=True))
    _persist(records)
    logger.info("First pass persisted (%d rows) - %d failed will be retried slowly.",
                len(records), stats_first["status"][scraper.FAILED])

    if not skip_js_rescue:
        failed_urls = [r["_url"] for r in records if r["_status"] == scraper.FAILED]
        if failed_urls:
            logger.info("Slow rescue pass for %d failed URL(s) (%d deep attempts each)...",
                        len(failed_urls), len(cfg.RESCUE_TIERS))
            rescued, _stats_rescue = asyncio.run(
                engine.run(failed_urls, tiers=cfg.RESCUE_TIERS))
            _persist(rescued)
            rescued_by_url = {r["_url"]: r for r in rescued}
            for record in records:
                if record["_url"] in rescued_by_url:
                    record.update(rescued_by_url[record["_url"]])

    merged_stats = scraper.ScrapeEngine._summarize(records, time.time() - started)
    scraper.ScrapeEngine._log_stats(merged_stats)
    logger.info("Pipeline finished (json mode: scraping_results + Google Sheet).")
    return records, merged_stats


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
        raise  # exit non-zero so the Cloud Run job is marked FAILED
