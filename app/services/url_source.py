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
    Explicit file wins (local dev); otherwise the DATABASE IS THE ONLY
    SOURCE. When the DB is configured we never silently fall back to a
    file - failures raise with a clear message instead of a confusing
    FileNotFoundError.
    """
    if file_path:
        return load_urls_from_file(file_path)
    if not db_manager.is_db_configured():
        # local development without a DB (urls.txt); but on Cloud Run the
        # database env vars MUST be set - give an actionable error instead
        # of a confusing FileNotFoundError.
        try:
            return load_urls_from_file()
        except FileNotFoundError:
            raise RuntimeError(
                "No database configured (set INSTANCE_DB/USER_DB/PASSWORD_DB/"
                "NAME_DB or MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD in the Cloud "
                "Run job env vars) and no URL file found."
            ) from None
    try:
        urls = load_urls_from_db()
    except Exception as exc:
        raise RuntimeError(
            f"Failed to load URLs from the database "
            f"({type(exc).__name__}: {exc}). Check INSTANCE_DB/USER_DB/"
            f"PASSWORD_DB/NAME_DB, LEGACY_TABLE and the service account "
            f"permissions."
        ) from exc
    if not urls:
        raise RuntimeError(
            f"No URLs found in {cfg.LEGACY_TABLE}.catalog_link - nothing to scrape."
        )
    return urls
