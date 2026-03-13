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




import gspread
from google.auth import default
from app.utils.logger import logger

def update_sheets_catalogo(result_list):
    """
    Sincroniza el result_list con la hoja 'Catalogo' de Google Sheets.
    """
    if not result_list:
        return

    try:
        # 1. Autenticación automática usando las credenciales del Cloud Job
        scopes = [
            'https://www.googleapis.com/auth/spreadsheets',
            'https://www.googleapis.com/auth/drive'
        ]
        credentials, _ = default(scopes=scopes)
        gc = gspread.authorize(credentials)

        # 2. Abrir el documento y la hoja
        spreadsheet_id = "11EF4fqrGlRzbkYBn8v0wxjUbV48ZTfVWfPKQJZDuFok"
        sh = gc.open_by_key(spreadsheet_id)
        worksheet = sh.worksheet("Catalogo")

        # 3. Obtener todos los datos actuales de la columna A (URLs) para mapear filas
        # Esto evita hacer peticiones por cada registro (muy lento)
        urls_in_sheet = worksheet.col_values(1)  # Columna A
        
        # 4. Preparar las actualizaciones
        # Formato de columnas: A:url, B:title, C:price, D:competitor, E:price_in_installments, 
        # F:image, G:timestamp, H:status, I:api_cost_total, J:remaining_credits
        
        updates = []
        
        for item in result_list:
            catalog_link = item.get('catalog_link')
            
            # Construimos la fila de datos
            row_data = [
                catalog_link,                          # A: url
                item.get('title'),                     # B
                item.get('price'),                     # C
                item.get('competitor'),                # D
                item.get('price_in_installments'),     # E
                item.get('image'),                     # F
                str(item.get('timestamp')),            # G (convertir a string para Sheets)
                item.get('status'),                    # H
                item.get('api_cost_total'),            # I
                item.get('remaining_credits', 0)       # J (si no viene, ponemos 0)
            ]

            if catalog_link in urls_in_sheet:
                # Si existe, encontramos el índice (sumamos 1 porque Sheets empieza en 1)
                row_idx = urls_in_sheet.index(catalog_link) + 1
                # Definimos el rango de la fila (de A a J)
                range_label = f"A{row_idx}:J{row_idx}"
                updates.append({'range': range_label, 'values': [row_data]})
            else:
                # Si no existe, podrías decidir si añadirlo al final
                # worksheet.append_row(row_data) # Opcional
                logger.warning(f"URL no encontrada en Sheet para actualizar: {catalog_link}")

        # 5. Ejecutar todas las actualizaciones en un solo lote (Batch Update)
        if updates:
            worksheet.batch_update(updates)
            logger.info(f"Google Sheet actualizado: {len(updates)} filas modificadas.")

    except Exception as e:
        logger.error(f"Error actualizando Google Sheets: {e}")

# --- Integración en tu función original ---

def load_scrap_gsheet(result_list):
    logger.info("Iniciando actualización en Google Sheets...")
    update_sheets_catalogo(result_list)