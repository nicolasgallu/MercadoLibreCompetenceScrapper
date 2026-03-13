from sqlalchemy import create_engine, text
from google.cloud.sql.connector import Connector
from app.settings.config import INSTANCE_DB, USER_DB, PASSWORD_DB, NAME_DB,  MELI_SCHMA
from app.utils.logger import logger
import gspread
import traceback
import sys
from google.auth import default
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
                SELECT distinct catalog_link FROM {NAME_DB}.scrapped_competence
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




def update_sheets_catalogo(result_list):
    """
    Versión con Debug Extendido para Google Sheets.
    """
    logger.info(f"--- Iniciando Sincronización con Sheets ---")
    
    if not result_list:
        logger.warning("La lista de resultados está vacía. Abortando.")
        return

    try:
        # 1. Verificar Identidad y Scopes
        logger.info("Paso 1: Obteniendo credenciales por defecto...")
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials, project_id = default(scopes=scopes)
        
        # Log clave: ¿Quién está intentando entrar?
        # Nota: Algunas cuentas no exponen el email directamente hasta refrescar, 
        # pero intentaremos mostrarlo.
        try:
            logger.info(f"Intentando con Service Account: {credentials.service_account_email}")
        except:
            logger.info("No se pudo obtener el email de la Service Account antes de autorizar.")

        # 2. Autorizar Cliente
        logger.info("Paso 2: Autorizando cliente de gspread...")
        gc = gspread.authorize(credentials)

        # 3. Abrir el documento
        spreadsheet_id = "11EF4fqrGlRzbkYBn8v0wxjUbV48ZTfVWfPKQJZDuFok"
        logger.info(f"Paso 3: Abriendo Spreadsheet ID: {spreadsheet_id}")
        sh = gc.open_by_key(spreadsheet_id)
        
        # 4. Acceder a la hoja
        logger.info("Paso 4: Accediendo a la pestaña 'Catalogo'...")
        worksheet = sh.worksheet("Catalogo")

        # 5. Mapear URLs actuales
        logger.info("Paso 5: Descargando valores de la Columna A para mapeo...")
        urls_in_sheet = worksheet.col_values(1)
        logger.info(f"Se encontraron {len(urls_in_sheet)} filas existentes en el Sheet.")

        # 6. Preparar actualizaciones
        updates = []
        logger.info(f"Paso 6: Procesando {len(result_list)} items del scraper...")
        
        for i, item in enumerate(result_list):
            # Log de seguridad para el primer item
            if i == 0:
                logger.debug(f"Estructura del primer item: {item.keys() if isinstance(item, dict) else 'NO ES DICT'}")

            catalog_link = item.get('catalog_link')
            if not catalog_link:
                logger.debug(f"Item #{i} ignorado: no tiene 'catalog_link'.")
                continue
            
            # Formateo de fila
            row_data = [
                catalog_link,
                item.get('title', ''),
                item.get('price', 0),
                item.get('competitor', ''),
                item.get('price_in_installments', ''),
                item.get('image', ''),
                str(item.get('timestamp', '')),
                item.get('status', ''),
                item.get('api_cost_total', 0),
                item.get('remaining_credits', 0)
            ]

            if catalog_link in urls_in_sheet:
                row_idx = urls_in_sheet.index(catalog_link) + 1
                updates.append({
                    'range': f"A{row_idx}:J{row_idx}",
                    'values': [row_data]
                })
            else:
                # Si quieres ver qué URLs faltan, descomenta la siguiente línea:
                # logger.debug(f"URL no encontrada en Sheet: {catalog_link}")
                pass

        # 7. Ejecutar Batch Update
        if updates:
            logger.info(f"Paso 7: Enviando batch_update para {len(updates)} filas...")
            worksheet.batch_update(updates)
            logger.info("¡Éxito! Google Sheet actualizado correctamente.")
        else:
            logger.warning("No se generaron actualizaciones. ¿Coinciden las URLs del scraper con las del Sheet?")

    except gspread.exceptions.APIError as e:
        logger.error(f"Error de API de Google: {e.response.text}")
    except Exception as e:
        # Aquí capturamos TODO con lujo de detalle
        logger.error("--- ERROR CRÍTICO EN GOOGLE SHEETS ---")
        logger.error(f"Tipo de excepción: {type(e).__name__}")
        logger.error(f"Mensaje de error: {str(e)}")
        logger.error("Traceback completo:")
        logger.error(traceback.format_exc())
    finally:
        logger.info("--- Fin del proceso de Sheets ---")

def load_scrap_gsheet(result_list):
    logger.info("Iniciando actualización en Google Sheets...")
    update_sheets_catalogo(result_list)