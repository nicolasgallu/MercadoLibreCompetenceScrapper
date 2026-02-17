# Mercado Libre Scraper with Scrapfly

This project scrapes product data from Mercado Libre Argentina using the [Scrapfly Python SDK](https://scrapfly.io/docs/python-sdk).  
It is designed to **mimic real human browsing** to bypass Mercado Libre’s strong anti-bot defenses.

---

## Overview of the Script

The script runs in two stages:

1. **Async multi-session scrape**:  
   - Splits URLs into small "sessions" (like a user browsing ~18 products).  
   - Runs several sessions in parallel (e.g. 5 at a time).  
   - Each session browses sequentially with pauses and retries.

2. **Rescue pass** (for failures):  
   - Any URLs that failed in the first pass are retried sequentially with **heavier settings** (longer waits, bigger timeouts, higher cost budget).  

At the end, all results are merged and written to JSON.

---

## Code Structure Explained

### 1. Imports & Setup
Uses Python standard libraries (`os`, `json`, `asyncio`, `datetime`, etc.),  
plus:
- **BeautifulSoup** for HTML parsing
- **Scrapfly SDK** for web scraping
- **dotenv** for API key loading
- **logging** for progress tracking

### 2. Logging
Configured at `INFO` level. All progress and errors print consistently.

### 3. Environment & Paths
- API key is read from `.env` (`SCRAPFLY_API_KEY`).
- Input URLs: `../database/urls.json`  
- Output JSON: `../database/scrap_results.json`

### 4. HTML Parser
`parse_product(html)` extracts:
- Title  
- Price  
- Competitor (seller)  
- Price in Installments  
- Main Image  

If the title is missing, returns `"Title not found"`.

### 5. URL Loading & Indexing
- Loads URLs into a list.  
- Adds **1-based indices** (`1, 2, 3...`) for progress tracking.  
- Example: `[(1, "url1"), (2, "url2"), ...]`

### 6. Base Scrapfly Config
```python
BASE_SC = dict(
    asp=True,
    render_js=True,
    wait_for_selector="h1.ui-pdp-title",
    proxy_pool="public_residential_pool",
    country="ar",
    lang=["es-AR","es"],
    session_sticky_proxy=True,
    retry=False,
    timeout=90000,
    cost_budget=30,
)
```
- ASP = anti-scraping protection  
- Headless browser enabled  
- Wait until title is visible  
- Residential proxies in Argentina  
- Spanish headers  
- 90s timeout, 30 credits budget  

### 7. Block-page Detection
`looks_blocked(text)` checks HTML for phrases like:
- "protegemos a nuestros usuarios"  
- "captcha"  
- "actividad inusual"  

If found, it retries with heavier settings.  
👉 You can **remove this** if you prefer simpler code (you’ll rely only on `"Title not found"`).

### 8. Single-URL Scrape
`scrape_one()`:
- Creates a `ScrapeConfig` per URL and session.  
- Escalates settings on retries:
  - Attempt 1: base  
  - Attempt 2: add waits, auto-scroll  
  - Attempt 3 (heavy): longer waits, bigger cost  
- Parses HTML, adds metadata:
  - `_url`, `_index`, `_attempt`, `_session`, `_timestamp`, `_api_cost`

Retries on:
- `"Title not found"` or `looks_blocked()`
- Scrapfly errors (`TIMEOUT`, `ASP`, `PROXY`, …)

### 9. Async Session Worker
`session_worker()`:
- Each session = dedicated Scrapfly client.  
- Browses sequentially, one URL at a time.  
- Uses `asyncio.to_thread` because SDK is blocking.  
- Adds **human-like pauses** between requests.

### 10. Async Orchestrator
`run_all()`:
- Splits full URL list into batches (e.g. 18 per session).  
- Runs several sessions concurrently (e.g. 5).  
- Collects all results.

### 11. Rescue Pass
`rescue_sequential()`:
- Retries failed URLs **one by one**.  
- Forces heavy settings (long wait, high timeout, bigger cost).  
- Ensures even stubborn pages are retried carefully.

### 12. Merging Results
`merge_results()`:
- Picks the best result for each index.  
- Prefers success from rescue if the first pass failed.  
- Returns results in original input order.

### 13. Writing Results
Saves final results to JSON:
```json
[
  {
    "Title": "Product Name",
    "Price": "12345",
    "Competitor": "Seller",
    "Price in Installments": "12x $1000",
    "Image": "https://...",
    "_url": "...",
    "_index": 1,
    "_attempt": 1,
    "_session": "mla-sess-1-...",
    "_timestamp": "2025-09-04T12:34:56.789Z",
    "_api_cost": "30"
  }
]
```

### 14. Entrypoint
`run_scrapping()`:
1. Loads URLs  
2. Runs async first pass  
3. Runs rescue pass if needed  
4. Merges results  
5. Saves JSON  

---

## Key Tunables
- **Speed vs. stealth**:
  - `urls_per_session=18` → how many URLs per browsing session  
  - `max_parallel_sessions=5` → how many sessions run at once
- **Patience & cost**:
  - `timeout` (90s base, 120s heavy)  
  - `cost_budget` (30 base, 45 heavy)
- **Resilience vs. simplicity**:
  - Keep `looks_blocked()` for accuracy  
  - Remove it for fewer lines

---

## Run

```bash
python your_scraper_file.py
```

Output is written to:
```
../database/scrap_results.json
```

Logs show progress like:

```
[INFO] [S1] START 1/265
[INFO] [S1] OK 1/265 (cost: 30)
[INFO] [S2] FAIL 19/265
[INFO] [RESCUE] OK 19/265
```

---

## Summary

This script:
- Scrapes Mercado Libre Argentina stealthily with Scrapfly  
- Parses core product info into JSON  
- Handles retries and blocks intelligently  
- Runs fast but human-like with async + pacing  
- Rescues failures sequentially with heavy settings  

It balances **speed**, **stealth**, and **resilience**, while keeping the code readable and tunable.
