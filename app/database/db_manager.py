"""
MySQL access layer.

Connects either to a plain MySQL server (MYSQL_HOST / MYSQL_USER / ...)
or to Google Cloud SQL via the GCP connector (INSTANCE_DB / ...), both
configured through env vars.

Table assumptions (placeholders until the real schema is designed — the
names are configurable via env):

  scraping_urls         (source)      id, url, status, created_at, updated_at
  scraping_results      (results)     id, url, status, data_scrapped JSON,
                                      created_at, updated_at   (UNIQUE KEY on url)
  scraping_field_config (config)      one row per extraction rule

See sql/schema.sql for the DDL + seed rows.
"""
import json

from sqlalchemy import create_engine, text

from app.settings import config as cfg
from app.utils.logger import logger

_engine = None


def _quoted(table: str) -> str:
    """Quote `schema`.`table` (or just `table`) safely for SQL."""
    return ".".join(f"`{part}`" for part in table.split(".") if part)


def is_db_configured() -> bool:
    plain = bool(cfg.MYSQL_HOST and cfg.MYSQL_USER)
    cloud = bool(cfg.INSTANCE_DB and cfg.USER_DB and cfg.PASSWORD_DB and cfg.NAME_DB)
    return plain or cloud


def get_engine():
    """
    Lazily create the SQLAlchemy engine. Not built at import time, so the
    rest of the app works fine without database credentials.
    """
    global _engine
    if _engine is None:
        if cfg.MYSQL_HOST and cfg.MYSQL_USER:
            db_name = cfg.MYSQL_NAME or cfg.NAME_DB or "scrapfly"
            url = (
                f"mysql+pymysql://{cfg.MYSQL_USER}:{cfg.MYSQL_PASSWORD or ''}"
                f"@{cfg.MYSQL_HOST}:{cfg.MYSQL_PORT}/{db_name}?charset=utf8mb4"
            )
            logger.info("Connecting to MySQL at %s:%s/%s",
                        cfg.MYSQL_HOST, cfg.MYSQL_PORT, db_name)
            _engine = create_engine(url, pool_pre_ping=True, pool_size=5, max_overflow=2)
        elif cfg.INSTANCE_DB:
            if cfg.SERVICE_ACCOUNT_FILE:
                import os as _os
                if _os.path.exists(cfg.SERVICE_ACCOUNT_FILE):
                    _os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = cfg.SERVICE_ACCOUNT_FILE
                    logger.info("Using service account file %s for Cloud SQL auth",
                                cfg.SERVICE_ACCOUNT_FILE)
                else:
                    # e.g. a stale path baked into the image: fall back to the
                    # Cloud Run attached service account (ADC)
                    logger.warning("SERVICE_ACCOUNT_FILE %s not found - "
                                   "using the Cloud Run attached service account (ADC)",
                                   cfg.SERVICE_ACCOUNT_FILE)
            from google.cloud.sql.connector import Connector

            connector = Connector()

            def getconn():
                return connector.connect(
                    cfg.INSTANCE_DB,
                    "pymysql",
                    user=cfg.USER_DB,
                    password=cfg.PASSWORD_DB,
                    db=cfg.NAME_DB,
                )

            _engine = create_engine(
                "mysql+pymysql://",
                creator=getconn,
                pool_pre_ping=True,
                pool_size=5,
                max_overflow=2,
            )
        else:
            raise RuntimeError(
                "No database configured: set MYSQL_HOST/MYSQL_USER/MYSQL_PASSWORD "
                "or INSTANCE_DB/USER_DB/PASSWORD_DB/NAME_DB"
            )
    return _engine


# ──────────────────────────────────────────────────────────────
# SOURCE: URLs to scrape
# ──────────────────────────────────────────────────────────────
def get_source_urls() -> list:
    """
    Extract the distinct URLs to scrape from the source table.

    Placeholder query: adapt the WHERE clause once the real table schema
    exists (e.g. filter by a status/active column).
    """
    table = _quoted(cfg.SCRAPE_URLS_TABLE)
    column = cfg.SCRAPE_URL_COLUMN
    with get_engine().connect() as conn:
        result = conn.execute(text(
            f"SELECT DISTINCT `{column}` AS url FROM {table} "
            f"WHERE `{column}` IS NOT NULL AND `{column}` != ''"
        ))
        urls = [row.url for row in result.mappings()]
        logger.info("Loaded %d URL(s) from %s.%s", len(urls), table, column)
        return urls



# ──────────────────────────────────────────────────────────────
# LEGACY FLAT TABLE (scrapped_competence)
#
# In legacy mode this table is BOTH the URL source and the results
# target. Kept exactly compatible with the pre-existing process.
# ──────────────────────────────────────────────────────────────
def get_legacy_urls() -> list:
    """
    Extract the distinct catalog URLs to scrape from the legacy table.
    """
    table = _quoted(cfg.LEGACY_TABLE)
    with get_engine().connect() as conn:
        result = conn.execute(text(
            f"SELECT DISTINCT catalog_link FROM {table} "
            f"WHERE catalog_link IS NOT NULL AND catalog_link != ''"
        ))
        urls = [row.catalog_link for row in result.mappings()]
        logger.info("Loaded %d URL(s) from %s.catalog_link", len(urls), table)
        return urls


def normalize_legacy_rows(records: list, remaining_credits=None) -> list:
    """
    records: engine records (best attempt per URL).
    Returns flat rows for the legacy table.

    Fixed columns: catalog_link, price (INT), price_in_installments,
    timestamp, status, api_cost_total, remaining_credits.
    Every OTHER field in the record (config-driven, e.g. title, competitor,
    image, subtitle, ...) is passed through with the same name, so adding a
    field = adding a config row + a DB column. No code change.
    """
    def clean_price(value):
        if value in (None, "", "n/a"):
            return 0
        digits = "".join(ch for ch in str(value) if ch.isdigit())
        return int(digits) if digits else 0

    def clean_installments(value):
        if value in (None, "n/a"):
            return ""
        return str(value).strip()

    rows = []
    for record in records:
        row = {
            "catalog_link": record.get("_url"),
            "price": clean_price(record.get("price")),
            "price_in_installments": clean_installments(record.get("price_in_installments")),
            "timestamp": record.get("_timestamp"),
            "status": record.get("_status"),
            "api_cost_total": int(record.get("_api_cost_total") or 0),
            "remaining_credits": remaining_credits,
        }
        # pass through every other scraped field (skip engine metadata keys)
        for key, value in record.items():
            if key.startswith("_") or key in row:
                continue
            row[key] = value
        rows.append(row)
    return rows


def _split_table(table: str):
    """Split 'schema.table' into (schema, table); schema falls back to the
    connected database name."""
    parts = table.split(".")
    if len(parts) == 2:
        return parts[0], parts[1]
    return (cfg.MYSQL_NAME or cfg.NAME_DB or ""), parts[-1]


def update_legacy_rows(rows: list) -> int:
    """
    UPDATE the legacy table keyed by catalog_link.

    The SET clause is built DYNAMICALLY from the columns that exist in the
    table, so a new scraped field only needs its config row + DB column.
    """
    table = cfg.LEGACY_TABLE
    if not rows:
        logger.info("No legacy rows to update.")
        return 0

    schema, tbl = _split_table(table)
    with get_engine().connect() as conn:
        result = conn.execute(text(
            "SELECT COLUMN_NAME AS col FROM information_schema.columns "
            "WHERE table_schema = :schema AND table_name = :tbl"
        ), {"schema": schema, "tbl": tbl})
        table_columns = {row.col for row in result.mappings()}

    # catalog_link is the WHERE key; everything else in the row that also
    # exists as a table column gets SET.
    set_cols = [c for c in rows[0].keys()
                if c in table_columns and c != "catalog_link"]
    skipped = [c for c in rows[0].keys() if c not in table_columns]
    if skipped:
        logger.info("Skipping fields without a DB column: %s", skipped)
    if not set_cols:
        logger.warning("No matching columns found in %s - nothing to update", table)
        return 0

    set_clause = ", ".join(f"`{c}` = :{c}" for c in set_cols)
    statement = text(
        f"UPDATE {_quoted(table)} SET {set_clause} WHERE catalog_link = :catalog_link"
    )
    payload = [{k: r[k] for k in [*set_cols, "catalog_link"] if k in r} for r in rows]
    with get_engine().begin() as conn:
        result = conn.execute(statement, payload)
        logger.info("Updated %d row(s) in %s (legacy mode, %d column(s))",
                    result.rowcount, table, len(set_cols))
        return result.rowcount


# ──────────────────────────────────────────────────────────────
# RESULTS: one row per URL, full record in a JSON column
# ──────────────────────────────────────────────────────────────
def build_result_rows(records: list) -> list:
    """
    records: engine records (best attempt per URL).
    Returns rows ready for the results table:
        url, status, data_scrapped (JSON string)
    """
    rows = []
    for record in records:
        rows.append({
            "url": record.get("_url"),
            "status": record.get("_status"),
            "data_scrapped": json.dumps(record, ensure_ascii=False),
        })
    return rows


def upsert_results(records: list) -> int:
    """
    Insert/update the results table (UNIQUE KEY on url required).

    data_scrapped stores the whole per-URL record as JSON:
    extracted fields + _status/_attempts/_api_cost_total/... metadata.
    """
    table = _quoted(cfg.SCRAPE_RESULTS_TABLE)
    rows = build_result_rows(records)
    if not rows:
        logger.info("No results to persist.")
        return 0

    statement = text(f"""
        INSERT INTO {table} (url, status, data_scrapped, created_at, updated_at)
        VALUES (:url, :status, :data_scrapped, NOW(), NOW())
        ON DUPLICATE KEY UPDATE
            status = VALUES(status),
            data_scrapped = VALUES(data_scrapped),
            updated_at = NOW()
    """)
    with get_engine().begin() as conn:
        conn.execute(statement, rows)
        logger.info("Persisted %d result row(s) to %s", len(rows), table)
        return len(rows)


# ──────────────────────────────────────────────────────────────
# CONFIG: field-extraction rules
# ──────────────────────────────────────────────────────────────
def load_field_config() -> list:
    """
    Read the enabled extraction rules from the config table.

    Returns a list of dicts (see app/services/rules.rows_to_rules for the
    expected keys). Returns [] when the table does not exist yet.
    """
    table = _quoted(cfg.SCRAPE_FIELDS_TABLE)
    try:
        with get_engine().connect() as conn:
            result = conn.execute(text(f"""
                SELECT field_name, kind, selectors, pattern, is_regex,
                       attribute, regex, regex_on_html,
                       exclude_class_contains, exclude_ancestor_class,
                       default_value, required, priority
                FROM {table}
                WHERE enabled = 1
                ORDER BY priority, id
            """))
            rows = [dict(row) for row in result.mappings()]
            logger.info("Loaded %d field-config row(s) from %s", len(rows), table)
            return rows
    except Exception as exc:
        logger.warning("Field-config table %s not available yet (%s)", table, exc)
        return []
