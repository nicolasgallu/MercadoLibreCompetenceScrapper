import requests
from app.settings.config import TOKEN_WHAPI, PHONE

def enviar_mensaje_whapi(mensaje):
    url = "https://gate.whapi.cloud/messages/text"
    payload = {
        "to": f"{PHONE}",
        "body": mensaje
    }
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "authorization": f"Bearer {TOKEN_WHAPI}"
    }
    
    res = requests.post(url, json=payload, headers=headers)
    return res.json()