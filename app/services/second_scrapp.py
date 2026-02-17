from scrapfly import ScrapflyClient, ScrapeConfig, ScrapflyScrapeError
from app.settings.config import SCRAP_KEY
from app.utils.logger import logger
from datetime import datetime
from bs4 import BeautifulSoup
import os, json, uuid, time

# ──────────────────────────────────────────────────────────────────────────────
# PATHS / CONSTANTS / VARIABLES
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATABASE_DIR = os.path.join(BASE_DIR, '../database')
FAILED_JSON_PATH = os.path.join(DATABASE_DIR, 'scrap_results.json')
OUTPUT_JSON_PATH = os.path.join(DATABASE_DIR, 'scrapping_failed_urls.json')
DISCARD_PHRASE = "Este producto no está disponible. Elige otra variante."
scrapped_results = []

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────
def now_ts():
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def write_results(rows):
    with open(OUTPUT_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2)

def read_failed():
    with open(FAILED_JSON_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Return only failed URLs
    return [row["_url"] for row in data if row.get("_status", "").lower() == "failed"]

def api_cost(response):
    return getattr(getattr(response, "response", None), "headers", {}).get("X-Scrapfly-Api-Cost", "n/a")

def parse_product(url, response):
    html = response.content
    soup = BeautifulSoup(html, "html.parser")

    # Check availability
    msg_el = soup.find(class_="ui-pdp-shipping-message__text")
    if msg_el and DISCARD_PHRASE in msg_el.get_text(strip=True):
        parsed = {
            "title": "n/a",
            "price": "",
            "competitor": "n/a",
            "price_in_installments": "n/a",
            "image": "n/a",
            "_url": url,
            "_timestamp": now_ts(),
            "_status": "discarded",
            "_api_cost": api_cost(response)
        }
        logger.warning(f"Discarded (not available)..")
        return parsed

    # Extract fields
    t = soup.find("h1", class_="ui-pdp-title")
    precio_container = soup.find("div", {"class": "ui-pdp-price__second-line"})
    p = precio_container.find("span", {"class": "andes-money-amount__fraction"}) if precio_container else None
    c = soup.find("h2", class_="ui-seller-data-header__title")
    q = soup.find("div", class_="ui-pdp-price__subtitles")
    img = soup.find("img", class_="ui-pdp-image")
    parsed = {
        "title": (t.text.strip() if t else "n/a"),
        "price": (p.text.strip() if p else ""),
        "competitor": (c.text.strip() if c else "n/a"),
        "price_in_installments": (q.text.strip() if q else "n/a"),
        "image": (img["src"] if (img and img.get("src")) else "n/a"),
        "_url": url,
        "_timestamp": now_ts(),
        "_status": "successed",
        "_api_cost": api_cost(response),
    }

    if parsed["title"] == "n/a":
        parsed["_status"] = "failed"
        logger.error(f"Failed to parse title.")
    else:
        logger.info(f"Successed retry..")
    
    return parsed

# ──────────────────────────────────────────────────────────────────────────────
# ATTEMPT HELPER
# ──────────────────────────────────────────────────────────────────────────────
def scrape_attempt(client, url, config, stage):
    try:
        # Remove timeout if retry=True
        if config.get("retry", False):
            config.pop("timeout", None)
        response = client.scrape(ScrapeConfig(url=url, **config))
        parsed = parse_product(url, response)
        parsed["retry_stage"] = stage
        parsed["failure_reason"] = None if parsed["_status"] in ["successed", "discarded"] else "parse_failed"
        scrapped_results.append(parsed)
        return parsed

    except ScrapflyScrapeError as e:
        logger.error("SCRAPFLY ERROR..")
        parsed = {
            "title": "n/a",
            "price": "",
            "competitor": "n/a",
            "price_in_installments": "n/a",
            "image": "n/a",
            "_url": url,
            "_timestamp": now_ts(),
            "_status": "SCRAPFLY ERROR",
            "_api_cost": "n/a",
            "retry_stage": stage,
            "failure_reason": getattr(e, "error_code", "") or type(e).__name__,
        }
        scrapped_results.append(parsed)
        return parsed

    except Exception as e:
        logger.error("UNEXPECTED ERROR..")
        parsed = {
            "title": "n/a",
            "price": "",
            "competitor": "n/a",
            "price_in_installments": "n/a",
            "image": "n/a",
            "_url": url,
            "_timestamp": now_ts(),
            "_status": "UNEXPECTED ERROR",
            "_api_cost": "n/a",
            "retry_stage": stage,
            "failure_reason": f"UNEXPECTED {type(e).__name__}",
        }
        scrapped_results.append(parsed)
        return parsed

# ──────────────────────────────────────────────────────────────────────────────
# CORE: scrape a single failed URL
# ──────────────────────────────────────────────────────────────────────────────
def scrape_one(client, session_id, url):
    base = dict(
        asp=True,
        render_js=True,
        wait_for_selector="h1.ui-pdp-title",
        proxy_pool="public_residential_pool",  # must be valid
        country="ar",
        lang=["es-AR", "es"],
        session_sticky_proxy=True,
        retry=True,
        session=session_id,
    )

    stages = [
        ("first_attempt", base.copy()),
        ("second_attempt", {**base, "rendering_wait": 10_000, "auto_scroll": True}),
        ("heavy_retry", {**base, "rendering_wait": 12_000, "auto_scroll": True, "session": f"HEAVY-{uuid.uuid4()}"}),
        ("rescue_pass", {**base, "rendering_wait": 15_000, "auto_scroll": True, "session": f"RESCUE-{uuid.uuid4()}"}),
        ("deep_rescue", {**base, "rendering_wait": 15_000, "wait_for_selector": None, "proxy_pool": "public_residential_pool", "session": f"DEEP-{uuid.uuid4()}"})
    ]

    for stage_name, cfg in stages:
        logger.info(f"{stage_name}..")
        out = scrape_attempt(client, url, cfg, stage_name)
        if out["_status"] in ["successed", "discarded"]:
            return
    logger.info(f"All retries failed..")

# ──────────────────────────────────────────────────────────────────────────────
# ORCHESTRATOR
# ──────────────────────────────────────────────────────────────────────────────
def scrape_all_failed(urls):
    client = ScrapflyClient(key=SCRAP_KEY)
    session_id = f"FAILED-{uuid.uuid4()}"
    logger.info(f"Retrying {len(urls)} failed URLs..")
    for url in urls:
        scrape_one(client, session_id, url)
        time.sleep(0.8)
    return scrapped_results

# ──────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY
# ──────────────────────────────────────────────────────────────────────────────
def scrap_urls_failed():
    logger.info("START - Second Scrapping Method.")
    failed_urls = read_failed()
    if not failed_urls:
        logger.info("END - Not failed URLs found.")
        return
    results = scrape_all_failed(failed_urls)
    write_results(results)
    logger.info("END - Second Scrapping Method.")
