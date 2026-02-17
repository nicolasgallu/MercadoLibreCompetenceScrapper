import os
from dotenv import load_dotenv
load_dotenv()

SCRAP_KEY = os.getenv("SCRAPFLY_API_KEY")

INSTANCE_DB=os.getenv("INSTANCE_DB")
USER_DB=os.getenv("USER_DB")
PASSWORD_DB=os.getenv("PASSWORD_DB")
NAME_DB=os.getenv("NAME_DB")

MELI_SCHMA=os.getenv("MELI_SCHMA")

TOKEN_WHAPI=os.getenv("TOKEN_WHAPI")
PHONE=os.getenv("PHONE")

SECRET_GUIAS=os.getenv("SECRET_GUIAS")