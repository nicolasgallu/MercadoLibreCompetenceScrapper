"""
LEGACY Google Sheets export (this process will be dropped eventually).

Updates the "Catalogo" tab of the configured spreadsheet. Columns A..K:

    url | title | subtitle | price | competitor | price_in_installments
    | image | timestamp | status | api_cost_total | remaining_credits

Rows are matched by the URL in column A and updated in place; URLs not
present in the sheet are appended at the bottom.

Auth: Google service account (SERVICE_ACCOUNT_FILE env). The service
account email must be shared as Editor on the spreadsheet.
"""
import os

from app.settings import config as cfg
from app.services.url_source import canonicalize
from app.utils.logger import logger

HEADERS = ["url", "title", "subtitle", "price", "competitor",
           "price_in_installments", "image", "timestamp", "status",
           "api_cost_total", "remaining_credits"]


def _row_values(row):
    return [
        row.get("catalog_link") or "",
        row.get("title") or "",
        row.get("subtitle") or "",
        row.get("price") if row.get("price") is not None else "",
        row.get("competitor") or "",
        row.get("price_in_installments") or "",
        row.get("image") or "",
        row.get("timestamp") or "",
        row.get("status") or "",
        row.get("api_cost_total") if row.get("api_cost_total") is not None else "",
        row.get("remaining_credits") if row.get("remaining_credits") is not None else "",
    ]


def _credentials_file():
    if cfg.SERVICE_ACCOUNT_FILE and os.path.exists(cfg.SERVICE_ACCOUNT_FILE):
        return cfg.SERVICE_ACCOUNT_FILE
    return os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")


def _client():
    """
    Build a gspread client from, in order:
      1) a service-account JSON file (local runs)
      2) Application Default Credentials (Cloud Run's attached SA)
    """
    import gspread

    creds_file = _credentials_file()
    if creds_file:
        return gspread.service_account(filename=creds_file)

    import google.auth

    credentials, _project = google.auth.default(
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    return gspread.authorize(credentials)


def update_catalogo(rows: list) -> int:
    """
    Update the Catalogo tab with the given flat rows. Never raises:
    failures are logged and the pipeline continues (legacy best-effort).
    """
    if not rows:
        return 0
    try:
        client = _client()
        sheet = client.open_by_url(cfg.GOOGLE_SHEET_URL).worksheet(cfg.GOOGLE_SHEET_TAB)

        # url -> row number (column A), matched raw OR canonicalized
        values = sheet.get_all_values()
        url_to_row = {}
        for i, line in enumerate(values):
            if i == 0:
                continue  # header row
            raw = (line[0] or "").strip()
            if not raw:
                continue
            url_to_row[raw] = i + 1
            canon = canonicalize(raw)
            if canon and canon not in url_to_row:
                url_to_row[canon] = i + 1

        updates = []
        appends = []
        for row in rows:
            url = (row.get("catalog_link") or "").strip()
            data = _row_values(row)
            row_no = url_to_row.get(url) or url_to_row.get(canonicalize(url))
            if row_no:
                updates.append({"range": f"A{row_no}:K{row_no}", "values": [data]})
            else:
                appends.append(data)

        if updates:
            sheet.batch_update(updates, value_input_option="USER_ENTERED")
        if appends:
            sheet.append_rows(appends, value_input_option="USER_ENTERED")
        logger.info("Google Sheet '%s' updated: %d row(s) updated, %d appended",
                    cfg.GOOGLE_SHEET_TAB, len(updates), len(appends))
        return len(updates) + len(appends)
    except Exception as exc:
        detail = getattr(exc, "response", None)
        detail = getattr(detail, "json", None)
        if callable(detail):
            try:
                detail = detail()
            except Exception:
                detail = None
        logger.warning("Google Sheets update failed (legacy step, continuing): "
                       "%s: %s | %s", type(exc).__name__, exc, detail)
        return 0
