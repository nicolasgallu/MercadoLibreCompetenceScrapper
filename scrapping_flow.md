# Scraping Flow (Mercado Libre + Scrapfly)

This document explains the **flow of the scraping process** implemented in the project.  
It focuses not only on the happy path but also on how failures are detected, retried, and rescued.

---

## High-Level Flow

1. **Load Service Account**
   - We load the S.A from the context where we run the Repo in GCR.

2. **Get Urls**


3. **Scrapping**

4. **Merging**

4. **Posting**

4. **Cleaning**


## Flow Example

### Case 1: Success on First Attempt
1. Scrapfly fetches product page.  
2. HTML contains `<h1 class="ui-pdp-title">`.  
3. Parser extracts title, price, etc.  
4. Saved as `"OK"` with `_attempt = 1`.

**Log:**
```
[S1] START 1/265 (attempt 1)
[S1] OK 1/265 (cost: 30)
```

---

### Case 2: Shield Page (Captcha or Block)
1. Scrapfly returns HTML but parser doesn’t find a title.  
2. `looks_blocked()` detects `"hemos detectado actividad inusual"`.  
3. Retry with attempt 2 (longer waits, auto-scroll).  
4. If still blocked, attempt 3 (heavy).  
5. If still failing, mark as `"FAIL"`.

**Log:**
```
[S3] START 37/265 (attempt 1)
[S3] RETRY-HINT 37/265 (parser/shield)
[S3] START 37/265 (attempt 2)
[S3] OK 37/265 (cost: 30)
```

---

### Case 3: Scrapfly Timeout
1. Scrapfly raises `ERR::SCRAPE::OPERATION_TIMEOUT`.  
2. Marked as transient → retry attempt 2.  
3. If succeeds, log as OK.  
4. If fails after 3 tries, mark `"ERROR"`.

**Log:**
```
[S2] START 19/265 (attempt 1)
[S2] RETRY 19/265 (TIMEOUT)
[S2] START 19/265 (attempt 2)
[S2] OK 19/265 (cost: 35)
```

---

### Case 4: Still Failing After 3 Attempts
1. All retries fail (blocked or error).  
2. Row is marked `"ERROR"`.  
3. Added to rescue queue.  
4. Rescue pass retries with **heavy config** sequentially.  
5. If rescue works → replaces original row.  
6. If still fails → saved as `"ERROR"`.

**Log:**
```
[S5] HARD FAIL 73/265 after 3 attempts
[RESCUE] START 73/265
[RESCUE] OK 73/265
```

---

## Final Output Example

```json
{
  "Title": "Samsung Galaxy S23",
  "Price": "599999",
  "Competitor": "Samsung Oficial",
  "Price in Installments": "12x $49.999",
  "Image": "https://...",
  "_url": "https://articulo.mercadolibre.com.ar/MLA-...",
  "_index": 1,
  "_session": "S1",
  "_attempt": 1,
  "_timestamp": "2025-09-04T12:34:56.789Z",
  "_api_cost": "30"
}
```

Failed entries may look like:

```json
{
  "Title": "ERROR",
  "Price": "",
  "Competitor": "",
  "Price in Installments": "",
  "Image": "",
  "_url": "https://articulo.mercadolibre.com.ar/MLA-...",
  "_index": 40,
  "_session": "S3",
  "_attempt": 3,
  "_timestamp": "2025-09-04T12:36:22.111Z",
  "_error": "TIMEOUT"
}
```

---

## Key Takeaways
- **Async sessions** simulate human browsing.  
- **Retries** handle temporary shields and slow loading.  
- **Rescue pass** salvages stubborn URLs.  
- **Final merge** ensures only the best data per URL is saved.  

This flow balances **speed**, **stealth**, and **reliability**.
