"""DB-layer tests: payload building, rule conversion, and a real MariaDB
integration test (skipped when no server is reachable)."""
import json

import pytest

import app.database.db_manager as dm
import app.settings.config as cfg
from app.services.rules import rows_to_rules

RECORD = {
    "title": "Producto",
    "price": "10.000",
    "competitor": "Vendedor",
    "price_in_installments": "3x $3.333",
    "image": "https://img/1.jpg",
    "_url": "https://www.mercadolibre.com.ar/p/MLA1",
    "_status": "successed",
    "_api_cost_total": 30,
    "_attempts": [{"stage": "tier0_res_js", "status": "successed"}],
}


def test_build_result_rows_json_column():
    rows = dm.build_result_rows([RECORD])
    assert len(rows) == 1
    row = rows[0]
    assert row["url"] == RECORD["_url"]
    assert row["status"] == "successed"
    data = json.loads(row["data_scrapped"])
    assert data["title"] == "Producto"
    assert data["_api_cost_total"] == 30
    assert data["_attempts"][0]["stage"] == "tier0_res_js"


def test_rows_to_rules_full():
    rows = [
        {"field_name": "title", "kind": "field", "selectors": '["h1.ui-pdp-title"]',
         "pattern": None, "is_regex": 0, "attribute": None, "regex": None,
         "regex_on_html": None, "exclude_class_contains": None,
         "exclude_ancestor_class": None, "default_value": "n/a", "required": 1},
        {"field_name": "image", "kind": "field", "selectors": '["img.ui-pdp-image"]',
         "pattern": None, "is_regex": 0, "attribute": "src", "regex": None,
         "regex_on_html": None, "exclude_class_contains": None,
         "exclude_ancestor_class": None, "default_value": "n/a", "required": 0},
        {"field_name": "discard", "kind": "discard_phrase",
         "selectors": None, "pattern": "no está disponible", "is_regex": 0,
         "attribute": None, "regex": None, "regex_on_html": None,
         "exclude_class_contains": None, "exclude_ancestor_class": None,
         "default_value": None, "required": 0},
        {"field_name": "block", "kind": "block_class",
         "selectors": None, "pattern": "message-card", "is_regex": 0,
         "attribute": None, "regex": None, "regex_on_html": None,
         "exclude_class_contains": None, "exclude_ancestor_class": None,
         "default_value": None, "required": 0},
    ]
    rules = rows_to_rules(rows)
    assert [f["field_name"] for f in rules["fields"]] == ["title", "image"]
    assert rules["fields"][1]["attribute"] == "src"
    assert rules["discard"] == ["no está disponible"]
    assert rules["block_classes"] == ["message-card"]


# ── real MariaDB integration (skipped when unavailable) ───────
def _db_is_reachable():
    import pymysql
    try:
        conn = pymysql.connect(host="127.0.0.1", port=3306, user="scraper",
                               password="scraperpass", database="scrapfly_test",
                               connect_timeout=2)
        conn.close()
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _db_is_reachable(), reason="local MariaDB not reachable")


@pytest.fixture()
def db(monkeypatch):
    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")
    dm._engine = None
    engine = dm.get_engine()
    assert dm.is_db_configured()
    yield engine
    engine.dispose()
    dm._engine = None


def _create_tables(engine):
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scraping_urls (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              url VARCHAR(700) NOT NULL,
              status VARCHAR(32) NOT NULL DEFAULT 'pending',
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id), UNIQUE KEY uq_url (url))"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scraping_results (
              id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
              url VARCHAR(700) NOT NULL,
              status VARCHAR(32) NOT NULL,
              data_scrapped JSON NOT NULL,
              created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
              PRIMARY KEY (id), UNIQUE KEY uq_url (url))"""))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scraping_field_config (
              id INT UNSIGNED NOT NULL AUTO_INCREMENT,
              field_name VARCHAR(64) NOT NULL,
              kind VARCHAR(32) NOT NULL DEFAULT 'field',
              selectors JSON NULL,
              pattern VARCHAR(255) NULL,
              is_regex TINYINT(1) NOT NULL DEFAULT 0,
              attribute VARCHAR(64) NULL,
              regex TEXT NULL,
              regex_on_html TEXT NULL,
              exclude_class_contains VARCHAR(128) NULL,
              exclude_ancestor_class VARCHAR(128) NULL,
              default_value VARCHAR(255) NULL,
              required TINYINT(1) NOT NULL DEFAULT 0,
              priority INT NOT NULL DEFAULT 0,
              enabled TINYINT(1) NOT NULL DEFAULT 1,
              PRIMARY KEY (id))"""))
        conn.execute(text("DELETE FROM scraping_urls"))
        conn.execute(text("DELETE FROM scraping_results"))
        conn.execute(text("DELETE FROM scraping_field_config"))


def test_full_db_roundtrip(db):
    from sqlalchemy import text
    _create_tables(db)

    # 1) seed source urls
    with db.begin() as conn:
        conn.execute(text("INSERT INTO scraping_urls (url) VALUES (:u)"),
                     {"u": "https://www.mercadolibre.com.ar/p/MLA1"})
        conn.execute(text("INSERT INTO scraping_urls (url) VALUES (:u)"),
                     {"u": "https://www.mercadolibre.com.ar/p/MLA2"})

    # 2) get_source_urls reads them
    urls = dm.get_source_urls()
    assert sorted(urls) == [
        "https://www.mercadolibre.com.ar/p/MLA1",
        "https://www.mercadolibre.com.ar/p/MLA2",
    ]

    # 3) upsert results (JSON column)
    record = dict(RECORD)
    n = dm.upsert_results([record])
    assert n == 1

    with db.connect() as conn:
        row = conn.execute(text(
            "SELECT url, status, data_scrapped, created_at, updated_at "
            "FROM scraping_results")).mappings().first()
    assert row["status"] == "successed"
    assert row["created_at"] is not None and row["updated_at"] is not None
    data = json.loads(row["data_scrapped"])
    assert data["title"] == "Producto"

    # 4) upsert again -> updated, not duplicated
    record["title"] = "Producto v2"
    dm.upsert_results([record])
    with db.connect() as conn:
        count = conn.execute(text("SELECT COUNT(*) AS c FROM scraping_results")).scalar()
        title = conn.execute(text(
            "SELECT JSON_UNQUOTE(JSON_EXTRACT(data_scrapped, '$.title')) AS t "
            "FROM scraping_results")).scalar()
    assert count == 1
    assert title == "Producto v2"

    # 5) field config roundtrip: only 'title' enabled -> rules contain only title
    with db.begin() as conn:
        conn.execute(text("""
            INSERT INTO scraping_field_config
              (field_name, kind, selectors, default_value, required, enabled)
            VALUES ('title', 'field', :sels, 'n/a', 1, 1)"""),
            {"sels": json.dumps(["h1.ui-pdp-title"])})
        conn.execute(text("""
            INSERT INTO scraping_field_config
              (field_name, kind, selectors, default_value, required, enabled)
            VALUES ('image', 'field', :sels, 'n/a', 0, 0)"""),  # disabled
            {"sels": json.dumps(["img.ui-pdp-image"])})

    rows = dm.load_field_config()
    rules = rows_to_rules(rows)
    assert [f["field_name"] for f in rules["fields"]] == ["title"]

    # 6) empty/disabled config -> load_rules FAILS LOUDLY (DB is the
    #    single source of truth when configured)
    from app.services.rules import load_rules
    with db.begin() as conn:
        conn.execute(text("UPDATE scraping_field_config SET enabled = 0"))
    with pytest.raises(RuntimeError):
        load_rules()


def test_json_pipeline_end_to_end():
    """New mode: URLs from scrapped_competence, results -> scraping_results JSON,
    and the Google Sheet still receives the flat rows."""
    import app.services.pipeline_scrapping as pipeline

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")
    monkeypatch.setattr(cfg, "SCRAPE_RESULTS_MODE", "json")
    dm._engine = None
    engine = dm.get_engine()
    _create_legacy_table(engine)
    _create_tables(engine)  # scraping_results + scraping_field_config
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO scrapped_competence (catalog_link) "
                              "VALUES (:u)"),
                         {"u": "https://www.mercadolibre.com.ar/p/MLA7"})
            conn.execute(text("""
                INSERT INTO scraping_field_config
                  (field_name, kind, selectors, default_value, required, enabled)
                VALUES ('title', 'field', :sels, 'n/a', 1, 1)"""),
                {"sels": json.dumps(["h1.ui-pdp-title"])})

        class StubEngine:
            def __init__(self, rules=None, max_concurrency=None, js_concurrency=None):
                self.rules = rules

            async def run(self, urls, skip_js_rescue=False):
                records = [{
                    "title": "JSON product", "price": "42.000",
                    "subtitle": "Nuevo | +10 vendidos",
                    "competitor": "JSON seller",
                    "price_in_installments": "6 cuotas de $ 9.000",
                    "image": "https://img/x.jpg",
                    "_url": urls[0], "_timestamp": "2026-09-26T10:00:00",
                    "_status": "successed", "_attempts": [], "_api_cost_total": 30,
                }]
                stats = {"total": len(urls), "status": {"successed": 1,
                                                         "discarded": 0, "failed": 0},
                         "credits": 30, "elapsed_seconds": 1.0, "per_second": 1.0,
                         "stage_attempts": {}, "error_types": {}}
                return records, stats

        monkeypatch.setattr(pipeline, "ScrapeEngine", StubEngine)
        monkeypatch.setattr(pipeline, "remain_budget", lambda: ("msg", 777))
        sheets_calls = []
        monkeypatch.setattr(pipeline.sheets, "update_catalogo",
                            lambda rows: sheets_calls.append(rows) or len(rows))

        records, stats = pipeline.run_pipeline()

        # results landed in the JSON table
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT status, data_scrapped FROM scraping_results")).mappings().first()
        assert row["status"] == "successed"
        data = json.loads(row["data_scrapped"])
        assert data["title"] == "JSON product"
        assert data["subtitle"] == "Nuevo | +10 vendidos"
        assert data["remaining_credits"] == 777
        # legacy table was NOT updated (new mode leaves it alone)
        with engine.connect() as conn:
            legacy = conn.execute(text(
                "SELECT title FROM scrapped_competence")).scalar()
        assert legacy is None
        # sheet still got the flat rows
        assert len(sheets_calls) == 1
        assert sheets_calls[0][0]["catalog_link"] == "https://www.mercadolibre.com.ar/p/MLA7"
        assert sheets_calls[0][0]["price"] == 42000
        assert sheets_calls[0][0]["subtitle"] == "Nuevo | +10 vendidos"
    finally:
        engine.dispose()
        dm._engine = None
        monkeypatch.undo()


# ── legacy mode: scrapped_competence flat table ──────────────
def _create_legacy_table(engine):
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS scrapped_competence (
              catalog_link TEXT NOT NULL,
              title VARCHAR(255) NULL,
              price INT NULL,
              competitor VARCHAR(100) NULL,
              price_in_installments VARCHAR(255) NULL,
              image TEXT NULL,
              timestamp DATETIME NULL,
              status VARCHAR(50) NULL,
              api_cost_total INT NULL,
              remaining_credits INT NULL,
              subtitle VARCHAR(255) NULL
            )"""))
        conn.execute(text("DELETE FROM scrapped_competence"))


def test_normalize_legacy_rows():
    records = [{
        "title": "Producto", "price": "199.999", "competitor": "Vendedor",
        "price_in_installments": "6 cuotas de $ 49.246", "image": "https://img/1.jpg",
        "_url": "https://www.mercadolibre.com.ar/p/MLA1",
        "_timestamp": "2026-09-25T04:49:16", "_status": "successed",
        "_api_cost_total": 30,
        "subtitle": "Nuevo | +100 vendidos",
    }, {
        "title": "n/a", "price": "", "competitor": "n/a",
        "price_in_installments": "n/a", "image": "n/a",
        "_url": "https://www.mercadolibre.com.ar/p/MLA2",
        "_timestamp": "2026-09-25T04:50:00", "_status": "failed",
        "_api_cost_total": 90,
    }]
    rows = dm.normalize_legacy_rows(records, remaining_credits=12345)
    assert rows[0]["price"] == 199999            # int, dots stripped
    assert rows[0]["price_in_installments"] == "6 cuotas de $ 49.246"
    assert rows[0]["api_cost_total"] == 30
    assert rows[0]["remaining_credits"] == 12345
    assert rows[0]["subtitle"] == "Nuevo | +100 vendidos"  # config field passes through
    assert rows[1]["price"] == 0                 # empty -> 0
    assert rows[1]["price_in_installments"] == ""  # n/a -> ""
    assert rows[1]["status"] == "failed"


def test_legacy_roundtrip():
    from sqlalchemy import text

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")
    dm._engine = None
    engine = dm.get_engine()
    _create_legacy_table(engine)
    try:
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO scrapped_competence (catalog_link) "
                              "VALUES (:u1), (:u2)"),
                         {"u1": "https://www.mercadolibre.com.ar/p/MLA1",
                          "u2": "https://www.mercadolibre.com.ar/p/MLA2"})
        urls = dm.get_legacy_urls()
        assert len(urls) == 2

        rows = dm.normalize_legacy_rows([{
            "title": "T1", "price": "1.234", "competitor": "C1",
            "price_in_installments": "3x $400", "image": "i1",
            "_url": urls[0], "_timestamp": "2026-09-25T04:49:16",
            "_status": "successed", "_api_cost_total": 30,
        }], remaining_credits=999)
        n = dm.update_legacy_rows(rows)
        assert n == 1

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT title, price, competitor, price_in_installments, "
                "status, api_cost_total, remaining_credits "
                "FROM scrapped_competence WHERE catalog_link = :u"),
                {"u": urls[0]}).mappings().first()
        assert row["title"] == "T1"
        assert row["price"] == 1234
        assert row["competitor"] == "C1"
        assert row["status"] == "successed"
        assert row["api_cost_total"] == 30
        assert row["remaining_credits"] == 999
    finally:
        engine.dispose()
        dm._engine = None
        monkeypatch.undo()


def test_legacy_pipeline_end_to_end():
    """Legacy mode: URLs from scrapped_competence, flat UPDATE, sheets patched."""
    import app.services.pipeline_scrapping as pipeline

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")
    monkeypatch.setattr(cfg, "SCRAPE_RESULTS_MODE", "legacy")
    dm._engine = None
    engine = dm.get_engine()
    _create_legacy_table(engine)
    _create_tables(engine)  # includes scraping_field_config
    try:
        from sqlalchemy import text
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO scrapped_competence (catalog_link) "
                              "VALUES (:u)"),
                         {"u": "https://www.mercadolibre.com.ar/p/MLA9"})
            conn.execute(text("""
                INSERT INTO scraping_field_config
                  (field_name, kind, selectors, default_value, required, enabled)
                VALUES ('title', 'field', :sels, 'n/a', 1, 1)"""),
                {"sels": json.dumps(["h1.ui-pdp-title"])})

        class StubEngine:
            def __init__(self, rules=None, max_concurrency=None, js_concurrency=None):
                self.rules = rules

            async def run(self, urls, skip_js_rescue=False):
                records = [{
                    "title": "Legacy product", "price": "42.000",
                    "competitor": "Legacy seller",
                    "price_in_installments": "6 cuotas de $ 9.000",
                    "image": "https://img/x.jpg",
                    "_url": urls[0], "_timestamp": "2026-09-25T05:00:00",
                    "_status": "successed", "_attempts": [], "_api_cost_total": 30,
                }]
                stats = {"total": len(urls), "status": {"successed": 1,
                                                         "discarded": 0, "failed": 0},
                         "credits": 30, "elapsed_seconds": 1.0, "per_second": 1.0,
                         "stage_attempts": {}, "error_types": {}}
                return records, stats

        monkeypatch.setattr(pipeline, "ScrapeEngine", StubEngine)
        monkeypatch.setattr(pipeline, "remain_budget",
                            lambda: ("msg", 777))
        sheets_calls = []
        monkeypatch.setattr(pipeline.sheets, "update_catalogo",
                            lambda rows: sheets_calls.append(rows) or len(rows))

        records, stats = pipeline.run_pipeline()

        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT title, price, status, remaining_credits "
                "FROM scrapped_competence WHERE catalog_link = :u"),
                {"u": "https://www.mercadolibre.com.ar/p/MLA9"}).mappings().first()
        assert row["title"] == "Legacy product"
        assert row["price"] == 42000
        assert row["status"] == "successed"
        assert row["remaining_credits"] == 777
        assert len(sheets_calls) == 1 and sheets_calls[0][0]["title"] == "Legacy product"
    finally:
        engine.dispose()
        dm._engine = None
        monkeypatch.undo()


# ── load_urls: DB is the only source when configured ─────────
def test_load_urls_raises_when_db_source_fails(monkeypatch):
    import app.database.db_manager as dm
    import app.services.url_source as us
    import app.settings.config as cfg

    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")

    def boom():
        raise RuntimeError("connection refused")
    monkeypatch.setattr(us.db_manager, "get_legacy_urls", boom)

    with pytest.raises(RuntimeError, match="Failed to load URLs from the database"):
        us.load_urls()


def test_load_urls_raises_when_db_has_no_urls(monkeypatch):
    import app.services.url_source as us
    import app.settings.config as cfg

    monkeypatch.setattr(cfg, "MYSQL_HOST", "127.0.0.1")
    monkeypatch.setattr(cfg, "MYSQL_PORT", 3306)
    monkeypatch.setattr(cfg, "MYSQL_USER", "scraper")
    monkeypatch.setattr(cfg, "MYSQL_PASSWORD", "scraperpass")
    monkeypatch.setattr(cfg, "MYSQL_NAME", "scrapfly_test")
    monkeypatch.setattr(us.db_manager, "get_legacy_urls", lambda: [])

    with pytest.raises(RuntimeError, match="No URLs found"):
        us.load_urls()


def test_load_urls_clear_error_when_no_db_and_no_file(monkeypatch):
    import app.services.url_source as us
    import app.settings.config as cfg

    monkeypatch.setattr(cfg, "INSTANCE_DB", "")
    monkeypatch.setattr(cfg, "MYSQL_HOST", "")
    monkeypatch.setattr(cfg, "URLS_FILE", "/nonexistent/urls.txt")

    with pytest.raises(RuntimeError, match="No database configured"):
        us.load_urls()
