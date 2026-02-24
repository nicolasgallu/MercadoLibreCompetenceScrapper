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
    Actualiza registros existentes en la tabla basándose en catalog_link.
    """
    table_name = f"{MELI_SCHMA}.scrapped_competence"

    if not result_list:
        logger.info("No hay datos para procesar.")
        return

    with engine.begin() as conn:
        # Ya no hacemos TRUNCATE, porque queremos conservar los datos para actualizarlos
        logger.info(f"Actualizando {len(result_list)} registros en {table_name}...")

        # Usamos la sintaxis de UPDATE filtrando por catalog_link
        update_query = text(f"""
            UPDATE {table_name} 
            SET 
                title = :title, 
                price = :price, 
                competitor = :competitor, 
                price_in_installments = :price_in_installments, 
                image = :image, 
                timestamp = :timestamp, 
                status = :status, 
                api_cost_total = :api_cost_total
            WHERE catalog_link = :catalog_link
        """)

        result = conn.execute(update_query, result_list)
        logger.info(f"Proceso completado. Filas afectadas: {result.rowcount}")


def calculate_metrics():
    """
    Calcula costos, márgenes y beneficios en la tabla scrapped_competence.
    """
    table_name = f"{MELI_SCHMA}.scrapped_competence"
    
    query = text(f"""
        UPDATE {table_name}
        SET 
            -- 1. Comisiones y Retornos
            ml_commision = selling_price * ml_commision_percentage,
            returns_cost = selling_price * estimated_returns_percentage,
            
            -- 2. Costo Total (Suma de todos los componentes de costo)
            total_costs = (
                product_cost + 
                (selling_price * ml_commision_percentage) + -- ml_commision
                shipping_cost + 
                packaging_cost + 
                (selling_price * estimated_returns_percentage) + -- returns_cost
                withholdings_gross_income_tax + 
                financial_cost
            ),
            
            -- 3. Utilidad Neta
            net_profit = selling_price - (
                product_cost + 
                (selling_price * ml_commision_percentage) + 
                shipping_cost + 
                packaging_cost + 
                (selling_price * estimated_returns_percentage) + 
                withholdings_gross_income_tax + 
                financial_cost
            ),
            
            -- 4. Márgenes (Evitando división por cero con NULLIF)
            net_margin_percentage = (selling_price - total_costs) / NULLIF(selling_price, 0),
            markup_percentage = (selling_price - total_costs) / NULLIF(product_cost, 0)
    """)

    try:
        with engine.begin() as conn:
            logger.info(f"Iniciando cálculo de métricas en {table_name}...")
            result = conn.execute(query)
            logger.info(f"Cálculos finalizados. Filas actualizadas: {result.rowcount}")
    except Exception as e:
        logger.error(f"Error al calcular métricas: {e}")
        raise