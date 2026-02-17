from project.utils.logger import logger
from project.database.db_manager import get_urls
from project.settings.config import SCRAP_KEY
import os, json, asyncio, uuid, random
from scrapfly import ScrapflyClient, ScrapeConfig
from datetime import datetime
from bs4 import BeautifulSoup

# ──────────────────────────────────────────────────────────────────────────────
# PATHS / CONSTANTS
# ──────────────────────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATABASE_DIR = os.path.join(BASE_DIR, "../database")
URLS_JSON = os.path.join(DATABASE_DIR, "urls.json")
RESULTS_JSON = os.path.join(DATABASE_DIR, "scrap_results.json")
DISCARD_PHRASE = "Este producto no está disponible. Elige otra variante."

# ──────────────────────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────────────────────



def now_ts():
    """Return current time formatted to seconds (ISO style)."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

def write_json(path, data):
    """Write a list/dict to JSON file with UTF-8 encoding."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def api_cost(response):
    """Returns the cost of the API call to Scrapfly"""
    cost = getattr(getattr(response, "response", None), "headers", {}).get("X-Scrapfly-Api-Cost", "n/a")
    return cost

# ──────────────────────────────────────────────────────────────────────────────
# CORE:
# ──────────────────────────────────────────────────────────────────────────────
async def scrape_one(client, url, discard_phrase):
    """
    Scrape one URL and return a tuple describing the result.
    """
    try:
        # create a unique session ID per scrape
        session_id = str(uuid.uuid4())

        # 1. Build request config
        cfg = ScrapeConfig(
            url=url,
            asp=True,
            render_js=True,
            wait_for_selector="h1.ui-pdp-title",
            proxy_pool="public_residential_pool",
            country="ar",
            lang=["es-AR", "es"],
            retry=False,
            timeout=90_000,
            cost_budget=30,
            session=session_id,
            session_sticky_proxy=True,
        )

        # 2. Execute scrapping
        res = await client.async_scrape(cfg)
        html = res.content
        soup = BeautifulSoup(html, "html.parser")

        # 3. Check availability
        msg_el = soup.find(class_="ui-pdp-shipping-message__text")
        if msg_el and discard_phrase in msg_el.get_text(strip=True):
            parsed = {
                "title": "n/a",
                "price": "",
                "competitor": "n/a",
                "price_in_installments": "n/a",
                "image": "n/a",
                "_url": url,
                "_timestamp": now_ts(),
                "_status": "discarded",
                "_api_cost": api_cost(res)
            }
            logger.warning(f"Discarded (not available)..")
            return parsed

        # 4. Extract fields & Parsing
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
            "_api_cost": api_cost(res),
        }

        # 5. Validate if Failed
        if parsed["title"] == "n/a":
            parsed["_status"] = "failed"
            logger.error(f"Failed to parse title.")
            return parsed
        
        # 6. Validate if Successed
        logger.info(f"Successed Scrapping..")
        return parsed
    
    except Exception:
        logger.error(f"Exception while scraping..")
        parsed = {
            "title": "n/a",
            "price": "",
            "competitor": "n/a",
            "price_in_installments": "n/a",
            "image": "n/a",
            "_url": url,
            "_timestamp": now_ts(),
            "_status": "failed",
            "_api_cost": "n/a",
        }
        return parsed

async def scrape_all(urls):
    """
    Orchestrates scraping for all URLs.
    Returns results, failures, and discarded URLs.
    """
    client = ScrapflyClient(key=SCRAP_KEY)
    sem = asyncio.Semaphore(5)  # limit concurrency

    results = []
    # --- shared counter ---
    counter = [0]  # mutable wrapper
    lock = asyncio.Lock()
    total = len(urls)

    async def job(url):
        async with sem:
            parsed = await scrape_one(client, url, DISCARD_PHRASE)
            results.append(parsed)
            async with lock:
                counter[0] += 1
                logger.info(f"[{counter[0]}/{total}] finished..")
            # add think-time delay
            await asyncio.sleep(random.uniform(1.5, 3.5))

    await asyncio.gather(*(job(u) for u in urls))
    return results

# ──────────────────────────────────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────────────────────────────────
def scrap_meli_urls():
    """
    Entry point: load URLs, scrape, and save results.
    """
    logger.info("START - First Scrapping Method.")
    urls = get_urls()
    #--- Run scraping ---
    results = asyncio.run(scrape_all(urls))
    write_json(RESULTS_JSON, results)
    logger.info("END - First Scrapping Method.")
