from sqlalchemy import create_engine, text
from google.cloud.sql.connector import Connector
from app.settings.config import INSTANCE_DB, USER_DB, PASSWORD_DB, NAME_DB,  MELI_SCHMA
from app.utils.logger import logger

##!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!
##CAMBIAR ESQUEMAS FIJOS A PARAMETROS 
##!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!

def getconn():
    connector = Connector() 
    return connector.connect(
        INSTANCE_DB,
        "pymysql",
        user=USER_DB,
        password=PASSWORD_DB,
        db=NAME_DB,
    )   

engine = create_engine(
        "mysql+pymysql://",
        creator=getconn,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=2,
    )

def get_urls():
    with engine.begin() as conn:
        logger.info("Extracting Catalog urls.")
        result = conn.execute(
            text(f"""
                SELECT catalog_link FROM {NAME_DB}.product_catalog_sync
                WHERE catalog_link is not null;
            """)
        )
        dataraw = [dict(row).get('catalog_link') for row in result.mappings()]
        if dataraw:
            logger.info(f"URL's ready to scrapp: {len(dataraw)}")
            return dataraw
        else:
            return []
        


def load_scrap(result_list):
    """
    """
    # Nombre de la tabla destino
    table_name = f"{MELI_SCHMA}.scrapped_competence"

    with engine.begin() as conn:
        # 1. Truncate explícito
        logger.info(f"Limpiando la tabla {table_name}...")
        conn.execute(text(f"TRUNCATE TABLE {table_name}"))

        # 2. Insert masivo (Bulk Insert)
        # Usamos nombres de parámetros que coincidan exactamente con las llaves de tus dicts
        logger.info(f"Insertando {len(result_list)} registros.")
        insert_query = text(f"""
            INSERT INTO {table_name} (
                title, price, competitor, price_in_installments, 
                image, catalog_link, timestamp, status, api_cost_total
            ) VALUES (
                :title, :price, :competitor, :price_in_installments, 
                :image, :catalog_link, :timestamp, :status, :api_cost_total
            )
        """)
        conn.execute(insert_query, result_list)
        logger.info("Carga completada con éxito.")