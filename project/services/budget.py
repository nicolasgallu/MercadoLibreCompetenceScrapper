import requests
from project.settings.config import SCRAP_KEY
def remain_budget():
    url = f"https://api.scrapfly.io/account?key={SCRAP_KEY}"
    response = requests.get(url)
    credist_left = response.json().get("subscription").get("usage").get("scrape").get("remaining")
    subscription_started_at = response.json().get("subscription").get("period").get("start")
    subscription_ended_at = response.json().get("subscription").get("period").get("end")
    data = f"Scrapping Finalizado\ncreditos restantes: {credist_left}\nfecha inicio de subscripcion: {subscription_started_at}\nfecha fin de subscripcion: {subscription_ended_at}"
    return data,credist_left
