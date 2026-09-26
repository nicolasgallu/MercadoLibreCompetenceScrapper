"""
Load the list of URLs to scrape.

Production: the source MySQL table (table/column configurable via env).
Local testing without a DB: a txt/csv file, canonicalized + deduplicated.
"""
import csv
import os
import re
import urllib.parse as up

from app.database import db_manager
from app.settings import config as cfg
from app.utils.logger import logger

_URL_RE = re.compile(r"https?://\S+")


def canonicalize(url: str) -> str:
    """
    Extract the URL (fixes corrupted lines like "buscarhttps://..."),
    strip the fragment and query string, keep scheme+host+path.
    """
    m = _URL_RE.search(url or "")
    if not m:
        return ""
    parts = up.urlsplit(m.group(0))
    return up.urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def load_urls_from_db() -> list:
    # URLs always come from scrapped_competence.catalog_link
    return db_manager.get_legacy_urls()


def load_urls_from_file(path: str = None) -> list:
    """
    One URL per line (or per CSV row); blank lines and #-comments are
    ignored. URLs are canonicalized and duplicates removed, preserving
    first-seen order.
    """
    path = path or cfg.URLS_FILE
    if not os.path.exists(path):
        raise FileNotFoundError(f"URL file not found: {path}")

    raw_urls = []
    if path.lower().endswith(".csv"):
        with open(path, "r", encoding="utf-8") as f:
            for row in csv.reader(f):
                for cell in row:
                    if _URL_RE.search(cell or ""):
                        raw_urls.append(cell.strip())
    else:
        with open(path, "r", encoding="utf-8") as f:
            raw_urls = [line.strip() for line in f]

    urls = []
    seen = set()
    skipped = 0
    for raw in raw_urls:
        if not raw or raw.startswith("#"):
            continue
        url = canonicalize(raw)
        if not url:
            skipped += 1
            continue
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    if skipped:
        logger.warning("Skipped %d unparseable line(s) in %s", skipped, path)
    logger.info("Loaded %d unique canonical URL(s) from %s (%d raw line(s))",
                len(urls), path, len(raw_urls))
    return urls


def load_urls(file_path: str = None) -> list:
    """
    Explicit file wins; otherwise the database when configured;
    otherwise the default URL file.
    """
    if file_path:
        return load_urls_from_file(file_path)
    if db_manager.is_db_configured():
        try:
            urls = load_urls_from_db()
            if urls:
                return urls
            logger.warning("Database returned no URLs - falling back to file source")
        except Exception as exc:
            logger.warning("Database source failed (%s) - falling back to file source", exc)
    return load_urls_from_file()
