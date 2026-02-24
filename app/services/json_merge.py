from app.utils.logger import logger
from app.database.db_manager import load_scrap,calculate_metrics
import pandas as pd
import json
import os

# ──────────────────────────────────────────────────────────────
# PATHS / CONSTANTS
# ──────────────────────────────────────────────────────────────
BASE_DIR = os.path.dirname(__file__)
DATABASE_DIR = os.path.join(BASE_DIR, '../database')
SCRAP_RESULTS_PATH = os.path.join(DATABASE_DIR, 'scrap_results.json')
FAILED_SCRAP_PATH = os.path.join(DATABASE_DIR, 'scrapping_failed_urls.json')
OUTPUT_PATH = os.path.join(DATABASE_DIR, 'merged_results.json')

# ──────────────────────────────────────────────────────────────
# HELPERS
# ──────────────────────────────────────────────────────────────
def load_json_list(path):
    """Load a JSON file as a list of dicts."""
    if not os.path.exists(path):
        return []
    
    if os.path.getsize(path) > 0:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else [data]
    else:
        return []

def safe_numeric(series):
    """Convert a series to numeric, coerce errors to 0."""
    return pd.to_numeric(series, errors="coerce").fillna(0)

# ──────────────────────────────────────────────────────────────
# CORE: build merged union with last record + sum of api cost
# ──────────────────────────────────────────────────────────────
def merge_scraping():

    # 1. Load JSONs
    scrap_rows  = load_json_list(SCRAP_RESULTS_PATH)
    failed_rows = load_json_list(FAILED_SCRAP_PATH)

    # 2. Convert to DataFrame
    df_scrap  = pd.DataFrame(scrap_rows)
    df_failed = pd.DataFrame(failed_rows)

    # If failed_rows is empty, just work with scrap_rows
    if df_failed.empty:
        df_all = df_scrap.copy()
    else:
        # Combine both sources
        df_all = pd.concat([df_scrap, df_failed], ignore_index=True)

    if df_all.empty:
        logger.info("No data to process.")
        return

    # 3.Sort Values by scrapped timestamp
    df_all = df_all.sort_values("_timestamp")

    # 4. Safe cast _api_cost to numeric
    df_all["_api_cost"] = safe_numeric(df_all.get("_api_cost", 0))

    # 5. Group by _url, keep last record per URL, sum _api_cost
    last_records = df_all.groupby("_url").last().reset_index()
    sum_costs = df_all.groupby("_url")["_api_cost"].sum().reset_index().rename(columns={"_api_cost": "_api_cost_total"})

    # 6. Merge last record with sum of api cost
    merged_df = pd.merge(last_records, sum_costs, on="_url")

    column_mapping = {
        "_url": "catalog_link",
        "_timestamp": "timestamp",
        "_status": "status",
        "_api_cost_total": "api_cost_total"
    }
    merged_df = merged_df.rename(columns=column_mapping)

    merged_df['price'] = merged_df['price'].apply(lambda x: 0 if x == '' else int(x.replace('.', '')))
    merged_df['price_in_installments'] = merged_df['price_in_installments'].apply(lambda x: 0 if x == 'n/a' else x)

    load_scrap(merged_df.to_dict(orient="records"))

    calculate_metrics()
