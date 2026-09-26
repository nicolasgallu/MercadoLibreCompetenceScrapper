"""
Isolate tests from the local .env: neutralize DB/service-account settings
BEFORE app.settings.config is imported, so unit tests never touch the real
Cloud SQL / Scrapfly credentials. DB integration tests re-enable what they
need via monkeypatch.
"""
import os

os.environ["SCRAPFLY_API_KEY"] = ""

for key, value in {
    "INSTANCE_DB": "", "USER_DB": "", "PASSWORD_DB": "", "NAME_DB": "", "MELI_SCHMA": "",
    "MYSQL_HOST": "", "MYSQL_PORT": "3306", "MYSQL_USER": "", "MYSQL_PASSWORD": "", "MYSQL_NAME": "",
    "SERVICE_ACCOUNT_FILE": "", "GOOGLE_APPLICATION_CREDENTIALS": "",
    "SCRAPE_RESULTS_MODE": "json",
    "LEGACY_TABLE": "scrapped_competence",
    "SCRAPE_URLS_TABLE": "scraping_urls", "SCRAPE_URL_COLUMN": "url",
    "SCRAPE_RESULTS_TABLE": "scraping_results", "SCRAPE_FIELDS_TABLE": "scraping_field_config",
}.items():
    os.environ[key] = value
