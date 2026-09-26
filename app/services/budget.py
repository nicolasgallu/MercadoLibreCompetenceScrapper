import requests

from app.settings.config import SCRAP_KEY


def remain_budget():
    """
    Query the Scrapfly account API for remaining credits and subscription
    period. Never raises: on failure returns (message, None).
    """
    if not SCRAP_KEY:
        return "Scrapping Finalizado\n(scrapfly key not set, budget unknown)", None
    url = f"https://api.scrapfly.io/account?key={SCRAP_KEY}"
    try:
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        payload = response.json()
        subscription = payload.get("subscription", {}) or {}
        usage = (subscription.get("usage", {}) or {}).get("scrape", {}) or {}
        remaining = usage.get("remaining")
        period = subscription.get("period", {}) or {}
        data = (
            f"Scrapping Finalizado\n"
            f"creditos restantes: {remaining}\n"
            f"fecha inicio de subscripcion: {period.get('start')}\n"
            f"fecha fin de subscripcion: {period.get('end')}"
        )
        return data, remaining
    except Exception as exc:
        return f"Scrapping Finalizado\n(budget query failed: {exc})", None
