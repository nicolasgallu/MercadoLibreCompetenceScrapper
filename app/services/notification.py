import requests

from app.settings.config import TOKEN_WHAPI, PHONE
from app.utils.logger import logger


def enviar_mensaje_whapi(mensaje):
    """Send a WhatsApp text via Whapi. Safe no-op when not configured."""
    if not TOKEN_WHAPI or not PHONE:
        logger.info("Whapi not configured - skipping WhatsApp notification")
        return None
    url = "https://gate.whapi.cloud/messages/text"
    payload = {"to": f"{PHONE}", "body": mensaje}
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {TOKEN_WHAPI}",
    }
    try:
        res = requests.post(url, json=payload, headers=headers, timeout=15)
        res.raise_for_status()
        return res.json()
    except Exception as exc:
        logger.warning("Whapi notification failed: %s", exc)
        return None
