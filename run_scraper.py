#!/usr/bin/env python3
"""
Standalone scraper runner.

Usage:
    python run_scraper.py [urls.txt] [options]

With a configured database (MYSQL_HOST/... or INSTANCE_DB/...):
    - URLs come from the source table (scraping_urls)
    - results go to the results table (scraping_results.data_scrapped JSON)
Without a database:
    - URLs come from urls.txt (or the file given as argument)
    - results are only reported in the summary (nothing persisted)

Options:
    --concurrency N      cap concurrent scrape requests (default: config/account)
    --js-concurrency N   concurrency for the deep JS rescue wave (default: 8)
    --no-js-rescue       skip the heavy JS rescue wave entirely
    --dump PATH.json     optionally dump the records to a JSON file (debug)
    --dry-run            validate URLs and print the plan without scraping
    -h, --help           show this help
"""
import argparse
import json
import sys

from app.services.pipeline_scrapping import run_pipeline
from app.services.scraper import ScrapeEngine
from app.services.url_source import load_urls_from_file
from app.utils.logger import logger


def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="Scrape Mercado Libre URLs with the fast Scrapfly engine.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("urls_file", nargs="?", default=None,
                        help="text file with one URL per line (optional when a DB is configured)")
    parser.add_argument("--concurrency", type=int, default=None,
                        help="cap concurrent scrape requests")
    parser.add_argument("--js-concurrency", type=int, default=None,
                        help="concurrency for the JS rescue wave")
    parser.add_argument("--no-js-rescue", action="store_true",
                        help="skip the heavy JS rescue wave")
    parser.add_argument("--limit", type=int, default=None, metavar="N",
                        help="scrape only the first N URLs (test runs)")
    parser.add_argument("--dump", type=str, default=None, metavar="PATH.json",
                        help="additionally dump records to a JSON file (debug only)")
    parser.add_argument("--dry-run", action="store_true",
                        help="only load/validate URLs and print the plan")
    return parser.parse_args(argv)


def dry_run(urls, args):
    from app.settings import config as cfg
    if not cfg.SCRAP_KEY:
        print("WARNING: SCRAPFLY_API_KEY is not set - showing defaults only.")
        concurrency, js_concurrency = cfg.MAX_CONCURRENCY, cfg.JS_CONCURRENCY
    else:
        engine = ScrapeEngine(max_concurrency=args.concurrency,
                              js_concurrency=args.js_concurrency)
        concurrency, js_concurrency = engine.concurrency, engine.js_concurrency
    print("─" * 60)
    print("DRY RUN (no scraping performed)")
    print("─" * 60)
    print(f"URLs            : {len(urls)}")
    print(f"Concurrency     : {concurrency} (wave A), {js_concurrency} (JS rescue)")
    print("Tier plan:")
    print("  1) residential + JS, 6s wait   ~30 credits/req")
    print("  2) residential + JS, 8s wait   ~30 credits/req (retry, fresh session)")
    print("  3) residential + JS, 10s wait  ~30 credits/req (deep rescue)")
    est = len(urls) * 30
    print(f"Credit estimate : ~{est} (one render per URL; +30 per retry)")
    print("─" * 60)


def report(stats):
    print("─" * 60)
    print("SCRAPE SUMMARY")
    print("─" * 60)
    status = stats.get("status", {})
    print(f"URLs            : {stats.get('total', 0)}")
    print(f"  successed     : {status.get('successed', 0)}")
    print(f"  discarded     : {status.get('discarded', 0)}  (product unavailable)")
    print(f"  failed        : {status.get('failed', 0)}")
    print(f"Credits spent   : ~{stats.get('credits', 0)}")
    print(f"Wall time       : {stats.get('elapsed_seconds', 0)}s  "
          f"({stats.get('per_second', 0)} url/s)")
    stage_attempts = stats.get("stage_attempts", {})
    if stage_attempts:
        print("Attempts by tier:")
        for stage, count in stage_attempts.items():
            print(f"  {stage:<24} {count}")
    error_types = stats.get("error_types", {})
    if error_types:
        print("Failure reasons:")
        for err, count in sorted(error_types.items(), key=lambda kv: -kv[1]):
            print(f"  {err:<30} {count}")
    print("─" * 60)


def main(argv=None):
    args = parse_args(argv if argv is not None else sys.argv[1:])

    from app.database import db_manager
    if args.urls_file:
        urls = load_urls_from_file(args.urls_file)
    elif db_manager.is_db_configured():
        from app.services.url_source import load_urls
        urls = load_urls()
    else:
        urls = load_urls_from_file()  # default urls.txt

    if not urls:
        logger.error("No URLs found.")
        return 2

    if args.dry_run:
        dry_run(urls, args)
        return 0

    try:
        records, stats = run_pipeline(
            urls=urls,
            skip_js_rescue=args.no_js_rescue,
            max_concurrency=args.concurrency,
            js_concurrency=args.js_concurrency,
            limit=args.limit,
        )
    except RuntimeError as exc:
        logger.error(str(exc))
        return 2

    if args.dump:
        with open(args.dump, "w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)
        logger.info("Debug dump written to %s", args.dump)

    report(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
