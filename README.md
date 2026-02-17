# 🛒 Mercado Libre Scraper Project

This project automates the process of scraping product information from Mercado Libre and saving the results to a Google Sheet.

## 📂 Project Structure

- `proyect/services/get_urls.py`  
  Fetches all product URLs from a Google Sheet and saves them to `proyect/database/urls.json`.

- `proyect/services/scrapp_mercadolibre.py`  
  Reads URLs from `proyect/database/urls.json`, scrapes product data, and saves results to `proyect/database/scrap_results.json`.

- `proyect/services/post_scrapp.py`  
  Reads `scrap_results.json` and posts the results to the "Scrapping" worksheet in your Google Sheet.

- `proyect/database/`  
  Stores intermediate and final data files (`urls.json`, `scrap_results.json`).

- `credenciales.json`  
  Google Service Account credentials for accessing Google Sheets.

## 🔍 What is Scraped
For each product URL, the following HTML elements are extracted:

- **Título (Title):** `<h1 class="ui-pdp-title">`  
- **Precio con Centavos (Price):** `<span class="andes-money-amount__fraction">`  
- **Competidor (Seller):** `<h2 class="ui-seller-data-header__title">`  
- **Precio en Cuotas (Installments):** `<div class="ui-pdp-price__subtitles">`  
- **Imagen (Image):** `<img class="ui-pdp-image">` (attribute: `src`)

## 🚀 How to Use
1. **Set up your Google Sheet** and share it with your service account (from `credenciales.json`).
2. **Configure environment variables** for your spreadsheet and worksheet names if needed.
3. **Run the scripts in order:**
   1. `get_urls.py` → saves URLs to the database.
   2. `scrapp_mercadolibre.py` → scrapes and saves results.
   3. `post_scrapp.py` → posts results to Google Sheets.

## 📝 Notes
- If an element is not found, a default value is used (e.g., "Sin título", "0", etc).
- The script is designed for the current Mercado Libre HTML structure. If Mercado Libre changes their layout, selectors may need to be updated.


