"""
Fast async scraping engine built on the Scrapfly SDK.

Why this is fast (vs. the legacy two-pass code):

1. Concurrency adapts to the plan: the engine asks the Scrapfly account
   API for `subscription.usage.scrape.concurrent_limit` and uses it
   (capped by config.MAX_CONCURRENCY). The legacy code hardcoded 5.
2. Retries run inside the same async pass with a short JS tier ladder:
   tier0 (rendering_wait 6s) -> tier1 (8s) -> deep rescue (10s).
   The legacy "second pass" retried failed URLs *sequentially* through
   5 escalating stages — the single biggest time sink.
3. Sessions are pooled per worker (one sticky session = one warm browser
   reused sequentially like a human browsing) and every retry gets a
   fresh session, so blocked URLs come back with a new browser + IP.

Note: Mercado Libre login-walls plain HTTP clients, so every tier here
uses residential proxy + ASP + JS rendering (measured ~15-25 s per URL).
That still beats the old code 3-4x by running 20+ renders concurrently
with zero per-URL sleeps and no sequential retry pass.
"""
import asyncio
import random
import time
import uuid
from datetime import datetime

from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyError

from app.settings import config as cfg
from app.services.parser import DEFAULT_RULES, classify_page, extract_fields
from app.utils.logger import logger

# statuses (kept lowercase for backwards compatibility with the DB values)
SUCCESSED = "successed"
DISCARDED = "discarded"
FAILED = "failed"


def now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def build_config(url: str, tier: dict, session: str) -> ScrapeConfig:
    """Build a ScrapeConfig for one attempt. `tier` values win over BASE_TIER."""
    params = dict(cfg.BASE_TIER)
    params.update(tier)
    params.pop("name", None)
    params["session"] = session
    return ScrapeConfig(url=url, **params)


def account_concurrency(client) -> int:
    """
    Ask the Scrapfly account API for the plan's concurrent scrape limit.

    Returns the limit, or 0 if it cannot be determined.
    """
    try:
        acct = client.account()
        sub = acct.get("subscription", {}) or {}
        usage = sub.get("usage", {}) or {}
        scrape = usage.get("scrape", {}) or {}
        limit = scrape.get("concurrent_limit") or sub.get("max_concurrency")
        project = acct.get("project", {}) or {}
        if not limit and project.get("concurrency_limit"):
            limit = project["concurrency_limit"]
        return int(limit) if limit else 0
    except Exception as exc:  # network hiccup, bad key, ... -> fall back
        logger.warning("Could not read account concurrency: %s", exc)
        return 0


class ThrottleGate:
    """
    Cooperative pause when Scrapfly returns HTTP 429 (account throttled:
    "too many requests on failed status code >= 100/min").

    Every worker that receives a 429 sleeps here, so the whole wave backs
    off instead of hammering the API and extending the throttle window.
    The same scrape attempt is then retried (no tier escalation).
    """

    def __init__(self, min_wait: float = 1.0):
        self._lock = asyncio.Lock()
        self._until = 0.0
        self.min_wait = min_wait

    async def note_throttled(self, retry_after=None):
        try:
            value = float(retry_after) if retry_after not in (None, "") else None
        except (TypeError, ValueError):
            value = None  # HTTP-date or garbage -> default
        wait = value if (value is not None and value >= 0) else 60.0
        async with self._lock:
            self._until = max(self._until, time.monotonic() + wait)
            pause = max(self.min_wait, self._until - time.monotonic())
        logger.warning("Scrapfly throttled (HTTP 429) - pausing %.0fs", pause)
        await asyncio.sleep(pause)


def _throttle_retry_after(res):
    """Return the Retry-After seconds when `res` is a 429 throttle response."""
    resp = getattr(res, "response", None)
    if resp is None:
        return None
    if getattr(resp, "status_code", None) != 429:
        return None
    headers = getattr(resp, "headers", None) or {}
    value = headers.get("Retry-After")
    try:
        return float(value) if value else 60.0
    except (TypeError, ValueError):
        return 60.0


class ScrapeEngine:
    """
    Scrapes a list of Mercado Libre URLs in tiered waves.

    Wave A (fast, high concurrency):
        tier0 JS (6s wait) -> tier1 JS (8s wait, fresh session)
    Wave B (rescue, low concurrency, only for URLs still failing):
        tier2 residential + JS rendering

    Every URL yields exactly one record (its best attempt). Per-attempt
    details are kept in `_attempts` for debugging; API cost is summed
    into `_api_cost_total`.
    """

    def __init__(self, key: str = None, client=None, max_concurrency: int = None,
                 js_concurrency: int = None, rules: dict = None):
        self.client = client
        if self.client is None:
            if not key:
                key = cfg.SCRAP_KEY
            if not key:
                raise ValueError("SCRAPFLY_API_KEY is not set (missing .env?)")
            # read timeout must cover the longest scrape timeout (90 s heavy tier)
            self.client = ScrapflyClient(key=key, web_scraping_api_read_timeout=130)

        # which fields to extract + how (from the DB config table or defaults)
        self.rules = rules or DEFAULT_RULES

        # backs off cooperatively when the account gets throttled (429)
        self.gate = ThrottleGate()

        # concurrency = min(config cap, account limit) with a sane floor
        cap = int(max_concurrency or cfg.MAX_CONCURRENCY)
        limit = account_concurrency(self.client)
        if limit > 0:
            self.concurrency = max(1, min(cap, limit))
            logger.info("Scrape concurrency: %d (cap %d, account limit %d)",
                        self.concurrency, cap, limit)
        else:
            self.concurrency = max(1, min(cap, cfg.FALLBACK_CONCURRENCY))
            logger.info("Scrape concurrency: %d (account limit unknown)", self.concurrency)

        self.js_concurrency = max(1, min(self.concurrency, int(js_concurrency or cfg.JS_CONCURRENCY)))

    # ──────────────────────────────────────────────────────────
    # PUBLIC API
    # ──────────────────────────────────────────────────────────
    async def run(self, urls: list, skip_js_rescue: bool = False):
        """
        Scrape all URLs and return (records, stats).

        records: list of final per-URL records in the SAME ORDER as urls.
        stats:   dict with status counts, per-stage counts, total credits, timing.
        """
        urls = list(urls)
        if not urls:
            return [], {"total": 0}

        started = time.time()

        # ── Wave A: cheap tiers, high concurrency ──
        wave_a, failed_idx = await self._wave(
            urls,
            tiers=[cfg.TIER_0_JS, cfg.TIER_1_JS],
            concurrency=self.concurrency,
            label="A",
        )

        # ── Wave B: JS rescue for the stubborn leftovers ──
        if failed_idx and not skip_js_rescue:
            logger.info("Rescue wave: %d URL(s) still failing -> tier2 deep JS retry",
                        len(failed_idx))
            carry = {
                i: {
                    "attempts": wave_a[i]["_attempts"],
                    "cost": wave_a[i].get("_api_cost_total", 0) or 0,
                }
                for i in failed_idx
            }
            wave_b, _ = await self._wave(
                [urls[i] for i in failed_idx],
                tiers=[cfg.TIER_2_JS],
                concurrency=self.js_concurrency,
                label="B",
                index_offset=failed_idx,
                carry=carry,
            )
            wave_a.update(wave_b)

        records = [wave_a[i] for i in range(len(urls))]
        stats = self._summarize(records, time.time() - started)
        self._log_stats(stats)
        return records, stats

    # ──────────────────────────────────────────────────────────
    # WAVES
    # ──────────────────────────────────────────────────────────
    async def _wave(self, urls, tiers, concurrency, label, index_offset=None, carry=None):
        """
        Scrape `urls` through `tiers` (in order) with `concurrency` workers.

        Returns (records_by_original_index, failed_original_indices).
        index_offset maps wave B positions back to wave A's original indices.
        carry: {orig_idx: {"attempts": [...], "cost": int}} to continue
        accounting across waves for retried URLs.
        """
        if index_offset is None:
            index_offset = list(range(len(urls)))
        carry = carry or {}

        sem = asyncio.Semaphore(concurrency)

        # One pooled session per worker: sessions keep a sticky IP and are
        # never used concurrently (avoids ERR::SESSION::CONCURRENT_ACCESS).
        session_pool = asyncio.Queue()
        for _ in range(concurrency):
            session_pool.put_nowait(f"w{label}-{uuid.uuid4().hex[:12]}")

        records = {}
        failed = []
        live = {SUCCESSED: 0, DISCARDED: 0, FAILED: 0}  # for progress logs only
        lock = asyncio.Lock()
        done = [0]
        wave_started = time.time()

        def _progress():
            elapsed = time.time() - wave_started
            if done[0] > 0:
                eta = elapsed / done[0] * (len(urls) - done[0])
                logger.info(
                    "wave %s: %d/%d done | ok=%d discarded=%d failed=%d | elapsed=%.0fs eta=%.0fs",
                    label, done[0], len(urls),
                    live[SUCCESSED], live[DISCARDED], live[FAILED], elapsed, eta,
                )
            else:
                logger.info("wave %s: 0/%d done | elapsed=%.0fs", label, len(urls), elapsed)

        async def worker(orig_idx, url):
            async with sem:
                session = await session_pool.get()
                try:
                    record = await self._scrape_url(
                        url, tiers, label, pooled_session=session,
                        carry=carry.get(orig_idx),
                    )
                finally:
                    session_pool.put_nowait(session)

                async with lock:
                    records[orig_idx] = record
                    live[record["_status"]] += 1
                    if record["_status"] == FAILED:
                        failed.append(orig_idx)
                    done[0] += 1
                    if done[0] % max(1, len(urls) // 20) == 0 or done[0] == len(urls):
                        _progress()

        tasks = [asyncio.create_task(worker(i, url)) for i, url in zip(index_offset, urls)]
        await asyncio.gather(*tasks)
        return records, failed

    # ──────────────────────────────────────────────────────────
    # PER-URL LOGIC
    # ──────────────────────────────────────────────────────────
    async def _scrape_url(self, url, tiers, wave_label, pooled_session, carry=None):
        carry = carry or {}
        attempts = list(carry.get("attempts", []))
        total_cost = carry.get("cost", 0) or 0
        base = len(attempts)  # attempts already made in previous waves
        last = None
        max_throttle_waits = 5  # per URL; beyond this we give up

        i = 0
        while i < len(tiers):
            tier = tiers[i]
            attempt_no = base + i + 1
            # First tier of each wave reuses the pooled (sticky) session;
            # every escalation gets a FRESH session = fresh IP.
            if i == 0 and pooled_session:
                session = pooled_session
            else:
                session = f"w{wave_label}-{uuid.uuid4().hex[:12]}"

            t0 = time.time()
            cfg_obj = build_config(url, tier, session)
            res = await self._do_scrape(cfg_obj)

            # account throttled? back off and retry the SAME tier (no cost
            # escalation, no attempt consumed)
            retry_after = _throttle_retry_after(res)
            if retry_after is not None:
                if max_throttle_waits <= 0:
                    record = self._finalize(url, res, tier, attempt_no, time.time() - t0)
                    record["_error"] = record["_error"] or "api_throttled"
                    attempts.append({
                        "stage": tier["name"], "attempt": attempt_no,
                        "status": FAILED, "error": "api_throttled",
                        "cost": record["_cost"], "duration_seconds": record["_duration_seconds"],
                    })
                    last = record
                    break
                max_throttle_waits -= 1
                await self.gate.note_throttled(retry_after)
                continue

            record = self._finalize(url, res, tier, attempt_no, time.time() - t0)

            attempts.append({
                "stage": tier["name"],
                "attempt": attempt_no,
                "status": record["_status"],
                "error": record["_error"],
                "cost": record["_cost"],
                "duration_seconds": record["_duration_seconds"],
            })
            total_cost += record["_cost"]

            if record["_status"] in (SUCCESSED, DISCARDED):
                record["_attempts"] = attempts
                record["_api_cost"] = record["_cost"]
                record["_api_cost_total"] = total_cost
                return record

            last = record
            i += 1
            # short human-ish pause before escalating the same URL
            await asyncio.sleep(random.uniform(0.3, 1.2))

        last["_attempts"] = attempts
        last["_api_cost"] = last["_cost"]
        last["_api_cost_total"] = total_cost
        return last

    async def _do_scrape(self, cfg_obj):
        """Run one scrape in a worker thread; never raises."""
        try:
            return await asyncio.to_thread(self.client.scrape, cfg_obj, True)
        except ScrapflyError as exc:
            # no_raise=True already returns the error envelope for API-level
            # errors; this catches SDK-side leftovers.
            api_response = getattr(exc, "api_response", None)
            return api_response if api_response is not None else exc
        except Exception as exc:
            return exc

    def _finalize(self, url, res, tier, attempt_no, duration):
        """Turn a scrape result into a record dict (never raises)."""
        headers = {}
        result = {}
        error = None
        status_code = None
        cost = 0
        content = ""

        if isinstance(res, Exception):
            error = type(res).__name__
        else:
            headers = getattr(getattr(res, "response", None), "headers", None) or {}
            api_result = getattr(res, "result", None) or {}
            result = api_result.get("result") or getattr(res, "scrape_result", None) or {}

            # API-level errors come in two shapes:
            #   nested: {"error": {"code": "ERR::..."}, ...}
            #   flat:   {"error_id": ..., "http_code": ..., "reason": ...}
            err_obj = api_result.get("error")
            envelope_error = (
                err_obj.get("code") if isinstance(err_obj, dict) else None
            ) if err_obj else None
            result_error = (
                result["error"].get("code") if result.get("error") else None
            )
            flat_error = (
                "api_%s" % str(api_result.get("reason")).replace(" ", "_")
                if not result and api_result.get("reason")
                else None
            )

            status_code = result.get("status_code") or api_result.get("http_code")
            error = envelope_error or result_error or flat_error
            content = result.get("content", "") or ""
            try:
                cost = int(headers.get("X-Scrapfly-Api-Cost", 0) or 0)
            except (TypeError, ValueError):
                cost = 0

        field_rules = self.rules.get("fields", [])
        fields = {rule["field_name"]: rule.get("default_value") or "" for rule in field_rules}
        status = FAILED

        if error is None:
            state, reason = classify_page(content, status_code or 0, self.rules)
            if state == "discarded":
                status, error = DISCARDED, reason
            elif state == "blocked":
                status, error = FAILED, reason
            else:
                fields = extract_fields(content, field_rules)
                missing = [
                    rule["field_name"] for rule in field_rules
                    if rule.get("required")
                    and fields.get(rule["field_name"]) == (rule.get("default_value") or "")
                ]
                if missing:
                    status, error = FAILED, f"{missing[0]}_not_found"
                else:
                    status = SUCCESSED
        elif status_code in (404, 410):
            status, error = DISCARDED, "http_%s" % status_code

        if status != SUCCESSED:
            # never leak half-parsed/garbage values on failed rows
            fields = {rule["field_name"]: rule.get("default_value") or ""
                      for rule in field_rules}

        if status == SUCCESSED:
            logger.debug("OK   [%s] %s (cost %d, %.1fs)", tier["name"], url, cost, duration)
        elif status == DISCARDED:
            logger.debug("SKIP [%s] %s (%s)", tier["name"], url, error)
        else:
            logger.debug("FAIL [%s] %s (%s, cost %d)", tier["name"], url, error, cost)

        record = {
            **fields,
            "_url": url,
            "_timestamp": now_ts(),
            "_status": status,
            "_stage": tier["name"],
            "_attempt": attempt_no,
            "_error": error,
            "_http_code": status_code,
            "_duration_seconds": round(duration, 1),
            "_cost": cost,
        }
        return record

    # ──────────────────────────────────────────────────────────
    # REPORTING
    # ──────────────────────────────────────────────────────────
    @staticmethod
    def _summarize(records, elapsed):
        stats = {
            "total": len(records),
            "status": {SUCCESSED: 0, DISCARDED: 0, FAILED: 0},
            "stage_attempts": {},
            "error_types": {},
            "credits": 0,
            "elapsed_seconds": round(elapsed, 1),
        }
        for record in records:
            stats["status"][record["_status"]] += 1
            stats["credits"] += record.get("_api_cost_total", 0) or 0
            for attempt in record.get("_attempts", []):
                stage = attempt["stage"]
                stats["stage_attempts"][stage] = stats["stage_attempts"].get(stage, 0) + 1
            if record["_status"] == FAILED:
                err = record.get("_error") or "unknown"
                stats["error_types"][err] = stats["error_types"].get(err, 0) + 1
        stats["per_second"] = round(stats["total"] / elapsed, 2) if elapsed > 0 else 0
        return stats

    def _log_stats(self, stats):
        status = stats["status"]
        logger.info(
            "DONE: %d URLs | successed=%d discarded=%d failed=%d | "
            "credits~%d | total %.0fs (%.2f url/s)",
            stats["total"], status[SUCCESSED], status[DISCARDED], status[FAILED],
            stats["credits"], stats["elapsed_seconds"], stats["per_second"],
        )
        if stats["error_types"]:
            logger.info("Failure reasons: %s", stats["error_types"])
