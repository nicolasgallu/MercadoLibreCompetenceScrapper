"""Engine tests with a fake Scrapfly client (no network, no API key)."""
import asyncio
import os

import pytest

from app.services.scraper import DISCARDED, FAILED, SUCCESSED, ScrapeEngine

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")
SUCCESS_HTML = """
<html><body>
<h1 class="ui-pdp-title">Producto de prueba</h1>
<div class="ui-pdp-price__second-line">
  <span class="andes-money-amount__fraction">10.000</span>
</div>
<h2 class="ui-seller-data-header__title">Vendedor Test</h2>
<div class="ui-pdp-price__subtitles">3x $3.333</div>
<img class="ui-pdp-image" src="https://img.test/1.jpg">
</body></html>
"""

BLOCKED_HTML = "<html><body><h1>Protegemos a nuestros usuarios</h1></body></html>"
WALL_HTML = '<html><body><p class="message-card">¡Hola! Para continuar, ingresa a<br/>tu cuenta</p></body></html>'
DISCARDED_HTML = ("<html><body><div class='ui-pdp-shipping-message__text'>"
                  "Este producto no está disponible por el momento.</div></body></html>")


class FakeResponse:
    def __init__(self, html=SUCCESS_HTML, status_code=200, cost=30, error_code=None):
        class FakeHTTPResponse:
            headers = {"X-Scrapfly-Api-Cost": str(cost)}

        self.response = FakeHTTPResponse()
        self.scrape_result = {
            "status_code": status_code,
            "content": html,
            "success": status_code < 300,
            "error": {"code": error_code} if error_code else None,
            "format": "text",
        }


class FakeClient:
    def __init__(self, handler, concurrent_limit=10):
        self.handler = handler
        self._concurrent_limit = concurrent_limit
        self.calls = []

    def scrape(self, cfg, no_raise=False):
        self.calls.append(cfg)
        return self.handler(cfg)

    def account(self):
        return {"subscription": {"usage": {"scrape": {"concurrent_limit": self._concurrent_limit}}}}


def run_engine(urls, handler, skip_js_rescue=False, max_concurrency=None, js_concurrency=None):
    client = FakeClient(handler)
    engine = ScrapeEngine(client=client, max_concurrency=max_concurrency,
                          js_concurrency=js_concurrency)
    records, stats = asyncio.run(engine.run(urls, skip_js_rescue=skip_js_rescue))
    return engine, client, records, stats


def test_concurrency_respects_account_limit():
    engine, client, _, _ = run_engine(["u1", "u2"], lambda cfg: FakeResponse())
    assert engine.concurrency == 10  # account says 10, cap default is 30


def test_concurrency_respects_cap():
    engine, client, _, _ = run_engine(["u1", "u2"], lambda cfg: FakeResponse(),
                                      max_concurrency=5)
    assert engine.concurrency == 5


def test_all_succeed_on_tier0():
    urls = [f"https://x.test/{i}" for i in range(5)]
    _, client, records, stats = run_engine(urls, lambda cfg: FakeResponse())
    assert [r["_url"] for r in records] == urls  # order preserved
    assert all(r["_status"] == SUCCESSED for r in records)
    assert all(r["_attempt"] == 1 and r["_stage"] == "tier0_res_js" for r in records)
    assert stats["status"][SUCCESSED] == 5
    assert len(client.calls) == 5
    assert all(cfg.render_js is True for cfg in client.calls)
    assert all(cfg.proxy_pool == "public_residential_pool" for cfg in client.calls)


def test_tier1_escalation_on_block():
    def handler(cfg):
        if cfg.rendering_wait == 6000:  # tier0
            return FakeResponse(html=WALL_HTML, cost=30)
        return FakeResponse(cost=30)  # tier1 succeeds

    _, client, records, stats = run_engine(["https://x.test/u1"], handler)
    record = records[0]
    assert record["_status"] == SUCCESSED
    assert record["_attempt"] == 2
    assert record["_stage"] == "tier1_res_js_retry"
    assert record["_api_cost_total"] == 60
    assert [a["stage"] for a in record["_attempts"]] == ["tier0_res_js", "tier1_res_js_retry"]
    assert len(client.calls) == 2
    # retry used a different (fresh) session
    assert client.calls[0].session != client.calls[1].session


def test_js_rescue_wave_when_everything_fails():
    def handler(cfg):
        if cfg.rendering_wait == 10000:  # deep rescue succeeds
            return FakeResponse(cost=30)
        return FakeResponse(html=WALL_HTML, cost=30)

    _, client, records, stats = run_engine(["https://x.test/u1"], handler)
    record = records[0]
    assert record["_status"] == SUCCESSED
    assert record["_stage"] == "tier2_res_js_deep"
    assert record["_api_cost_total"] == 90
    assert len(record["_attempts"]) == 3


def test_skip_js_rescue():
    _, client, records, stats = run_engine(
        ["https://x.test/u1"], lambda cfg: FakeResponse(html=WALL_HTML),
        skip_js_rescue=True)
    record = records[0]
    assert record["_status"] == FAILED
    assert record["_error"] == "shield_detected"
    assert len(record["_attempts"]) == 2
    assert len(client.calls) == 2


def test_discarded_does_not_retry():
    _, client, records, stats = run_engine(
        ["https://x.test/u1"], lambda cfg: FakeResponse(html=DISCARDED_HTML))
    assert records[0]["_status"] == DISCARDED
    assert len(client.calls) == 1


def test_404_is_discarded():
    _, client, records, stats = run_engine(
        ["https://x.test/u1"], lambda cfg: FakeResponse(html="", status_code=404))
    assert records[0]["_status"] == DISCARDED
    assert records[0]["_error"] == "http_404"


def test_failed_when_no_title():
    _, client, records, stats = run_engine(
        ["https://x.test/u1"],
        lambda cfg: FakeResponse(html="<html><body><p>sin titulo</p></body></html>"))
    assert records[0]["_status"] == FAILED
    assert records[0]["_error"] == "title_not_found"


def test_empty_urls():
    engine = ScrapeEngine(client=FakeClient(lambda cfg: FakeResponse()))
    records, stats = asyncio.run(engine.run([]))
    assert records == [] and stats["total"] == 0


# ── real-dom fixture tests ──────────────────────────────────────
def test_real_catalog_available_page():
    html = open(os.path.join(FIXTURES, "catalog_available.html"),
                encoding="utf-8", errors="ignore").read()
    _, client, records, stats = run_engine(
        ["https://www.mercadolibre.com.ar/p/MLA44716589"],
        lambda cfg: FakeResponse(html=html))
    record = records[0]
    assert record["_status"] == SUCCESSED
    assert record["title"] == "Consola Novik Neo NVK-I08BT de mezcla"
    assert record["price"] == "199.999"          # buy-box price, not a related card
    assert record["image"].startswith("https://http2.mlstatic.com")
    # catalog pages hide seller + installments in SSR
    assert record["competitor"] == "n/a"
    assert record["price_in_installments"] == "n/a"


def test_real_catalog_unavailable_page():
    html = open(os.path.join(FIXTURES, "catalog_unavailable.html"),
                encoding="utf-8", errors="ignore").read()
    _, client, records, stats = run_engine(
        ["https://www.mercadolibre.com.ar/p/MLA46807207"],
        lambda cfg: FakeResponse(html=html))
    assert records[0]["_status"] == DISCARDED
    assert records[0]["_error"] == "product_unavailable"


def test_real_wall_page_is_blocked():
    html = open(os.path.join(FIXTURES, "wall.html"),
                encoding="utf-8", errors="ignore").read()
    _, client, records, stats = run_engine(
        ["https://articulo.mercadolibre.com.ar/MLA-1"],
        lambda cfg: FakeResponse(html=html))
    assert records[0]["_status"] == FAILED
    assert records[0]["_error"] == "shield_detected"


def test_failed_record_fields_are_defaults():
    """A failed page must not leak half-parsed values into the record."""
    html = ("<html><body><p>Vendido por '},garbage...'</p>"
            "<span class='andes-money-amount__fraction'>9.999</span></body></html>")
    _, client, records, stats = run_engine(
        ["https://x.test/u1"], lambda cfg: FakeResponse(html=html))
    record = records[0]
    assert record["_status"] == FAILED
    assert record["title"] == "n/a"
    assert record["competitor"] == "n/a"
    assert record["price"] == ""
    assert record["price_in_installments"] == "n/a"
    assert record["image"] == "n/a"


# ── throttle (HTTP 429) handling ─────────────────────────────
class ThrottledResponse:
    """Mimics a Scrapfly 429 envelope (flat error, Retry-After header)."""
    def __init__(self, retry_after="0"):
        class FakeHTTPResponse:
            status_code = 429
            headers = {"Retry-After": retry_after, "X-Scrapfly-Api-Cost": "0"}
        self.response = FakeHTTPResponse()
        self.result = {"reason": "throttled", "http_code": 429}


def test_429_pauses_and_retries_same_tier():
    import time
    pauses = []

    class Client(FakeClient):
        def __init__(self):
            super().__init__(handler=None)
            self.n = 0

        def scrape(self, cfg, no_raise=False):
            self.calls.append(cfg)
            self.n += 1
            if self.n <= 2:
                return ThrottledResponse(retry_after="0")
            return FakeResponse()  # succeeds on 3rd try

    client = Client()
    engine = ScrapeEngine(client=client, max_concurrency=5)
    engine.gate.min_wait = 0  # no real sleep in tests

    records, stats = asyncio.run(engine.run(["https://x.test/u1"]))
    record = records[0]
    assert record["_status"] == SUCCESSED
    assert record["_attempt"] == 1          # throttle retries do NOT consume attempts
    assert len(record["_attempts"]) == 1
    assert record["_stage"] == "tier0_res_js"
    assert len(client.calls) == 3           # 2x 429 + 1 success


def test_429_gives_up_after_max_waits():
    client = FakeClient(lambda cfg: ThrottledResponse(retry_after="0"))
    engine = ScrapeEngine(client=client, max_concurrency=5)
    engine.gate.min_wait = 0

    records, stats = asyncio.run(engine.run(["https://x.test/u1"], skip_js_rescue=True))
    record = records[0]
    assert record["_status"] == FAILED
    assert record["_error"] == "api_throttled"
    # 1 initial + 5 throttle waits = 6 calls, then gives up
    assert len(client.calls) == 6


def test_run_with_tiers_override_runs_only_those_tiers():
    """The slow rescue pass: only the given tiers run, no wave B."""
    from app.settings import config as cfg

    waits = []

    def handler(cfg_obj):
        waits.append(cfg_obj.rendering_wait)
        return FakeResponse(html=WALL_HTML, cost=30)

    client = FakeClient(handler)
    engine = ScrapeEngine(client=client, max_concurrency=5)
    records, stats = asyncio.run(
        engine.run(["https://x.test/u1"], tiers=[cfg.TIER_2_JS]))

    assert len(client.calls) == 1                       # exactly one attempt
    assert client.calls[0].rendering_wait == 10_000     # the deep tier only
    assert records[0]["_status"] == FAILED              # no wave B, no tier0/1
