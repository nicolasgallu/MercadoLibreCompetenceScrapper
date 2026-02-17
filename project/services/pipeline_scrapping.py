from project.services.first_scrapp import scrap_meli_urls   
from project.services.second_scrapp import scrap_urls_failed    
from project.services.json_merge import merge_scraping
from project.services.budget import remain_budget
from project.services.notification import enviar_mensaje_whapi

def scrapping():
    enviar_mensaje_whapi("comenzando scrapping")
    scrap_meli_urls()
    scrap_urls_failed()
    budget_data, credist_left = remain_budget()
    merge_scraping()
    enviar_mensaje_whapi(budget_data)