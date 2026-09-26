"""
Parallelism sanity test: the wave must actually fan out across workers.

900 fake URLs, 100 ms per scrape, concurrency 30:
    serial would take ~90 s, the wave should finish in a few seconds.
Timing assertions are generous on purpose to stay CI-safe.
"""
import asyncio
import time

from app.services.scraper import SUCCESSED, ScrapeEngine

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


class SlowResponse:
    def __init__(self, cost=1):
        class FakeHTTPResponse:
            headers = {"X-Scrapfly-Api-Cost": str(cost)}
        self.response = FakeHTTPResponse()
        self.scrape_result = {
            "status_code": 200,
            "content": SUCCESS_HTML,
            "success": True,
            "error": None,
            "format": "text",
        }


class SlowClient:
    def __init__(self, delay, concurrent_limit=100):
        self.delay = delay
        self._concurrent_limit = concurrent_limit

    def scrape(self, cfg, no_raise=False):
        time.sleep(self.delay)  # called in a worker thread, so it blocks one slot
        return SlowResponse()

    def account(self):
        return {"subscription": {"usage": {"scrape": {"concurrent_limit": self._concurrent_limit}}}}


def test_wave_fans_out_across_workers():
    n = 900
    delay = 0.1
    concurrency = 30
    client = SlowClient(delay)
    engine = ScrapeEngine(client=client, max_concurrency=concurrency)
    assert engine.concurrency == concurrency

    t0 = time.time()
    records, stats = asyncio.run(engine.run([f"https://x.test/{i}" for i in range(n)]))
    wall = time.time() - t0

    assert len(records) == n
    assert stats["status"][SUCCESSED] == n

    serial_time = n * delay
    print(f"wall={wall:.1f}s (serial would be {serial_time:.0f}s, "
          f"speedup {serial_time / wall:.0f}x)")
    # should be ~ n/concurrency * delay + overhead; allow generous slack
    assert wall < max(serial_time / 3, 15)
