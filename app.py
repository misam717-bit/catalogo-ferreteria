# --- Importaciones de base y librerías ---
import csv
import io
# Se importa el driver de PostgreSQL
import psycopg2 
from psycopg2 import sql 
# IMPORTACIÓN CRÍTICA: Necesario para usar el cursor de diccionario (DictCursor)
import psycopg2.extras 
import secrets
import math 
import os
from flask import Flask, render_template, request, url_for, flash, redirect
# Librería para cargar variables de entorno
from dotenv import load_dotenv

# Importaciones de Cloudinary para manejo de archivos
import cloudinary
import cloudinary.uploader
import cloudinary.api

# Cargar las variables de entorno del archivo .env
# En un entorno de producción como Render, esto se omite ya que las variables se cargan automáticamente
load_dotenv()

# --- CONFIGURACIÓN DE FLASK ---
app = Flask(__name__)
# Usa la variable de entorno SECRET_KEY si existe, sino usa una clave fuerte por defecto
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_fuerte_y_aleatoria_para_prod')

# Define cuántos productos quieres mostrar por página
PRODUCTS_PER_PAGE = 20

# --- CONFIGURACIÓN DE CLOUDINARY ---
cloudinary.config(
    cloud_name=os.environ.get('CLOUD_NAME'),
    api_key=os.environ.get('API_KEY'),
    api_secret=os.environ.get('API_SECRET'),
    secure=True
)
CLOUDINARY_FOLDER = "catalogo-ferreteria-nea"

# =======================================================
# --- FUNCIONES CRÍTICAS PARA POSTGRESQL EN RENDER ---
# =======================================================

def get_db_connection():
    """
    Establece la conexión a la base de datos PostgreSQL usando la URL
    proporcionada por Render (DATABASE_URL).
    """
    try:
        # Se obtiene la cadena de conexión de las variables de entorno de Render
        db_url = os.environ.get("DATABASE_URL")
        
        if not db_url:
            raise ValueError("DATABASE_URL no está configurada.")
        
        # FIX MANTENIDO: Limpiar la URL de cualquier espacio, comilla doble o simple indeseada
        # Esto previene errores de conexión debido a un mal parseo de la variable de entorno.
        db_url = db_url.strip().strip('"').strip("'")
            
        # Conexión a PostgreSQL. 
        conn = psycopg2.connect(db_url)
        return conn
        
    except Exception as e:
        print(f"Error al conectar con la base de datos PostgreSQL: {e}")
        # En caso de error, la aplicación no debería seguir, por eso levantamos el error.
        raise

def init_db():
    """
    Inicializa el esquema de la base de datos.
    """
    conn = None
    cur = None
    try:
        conn = get_db_connection()
        # *** FIX CRÍTICO: Se debe usar un cursor para ejecutar el SQL ***
        cur = conn.cursor()
        
        # 1. Crear la tabla 'productos' si no existe
        # Se utiliza sql.SQL() para una sintaxis segura en psycopg2
        cur.execute(sql.SQL("""
            CREATE TABLE IF NOT EXISTS productos (
                id SERIAL PRIMARY KEY,
                codigo TEXT UNIQUE NOT NULL, -- Se asegura que el código no sea NULL
                nombre TEXT NOT NULL,
                descripcion TEXT,
                precio REAL NOT NULL,
                imagen_url TEXT
            );
        """))
        
        conn.commit()
    except Exception as e:
        print(f"Error al inicializar la base de datos: {e}")
    finally:
        if cur: cur.close() 
        if conn: conn.close()

# Asegurar que la DB se inicializa con el schema correcto
# Esto se ejecuta una sola vez al cargar la aplicación
with app.app_context():
    init_db()

# Nueva función auxiliar para buscar productos por código (psycopg2)
def get_product_by_codigo(codigo):
    conn = get_db_connection()
    # USANDO DICTCURSOR: Esto permite que product sea un objeto tipo diccionario
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # Se usa la sintaxis de %s para psycopg2 en lugar de ?
    cur.execute('SELECT * FROM productos WHERE codigo = %s', (codigo,))
    product = cur.fetchone()
    conn.close()
    # Si el producto existe, retornamos un objeto que se comporta como Row (DictCursor)
    return dict(product) if product else None

# Función auxiliar para obtener producto por ID (psycopg2)
def get_product(product_id):
    conn = get_db_connection()
    # USANDO DICTCURSOR
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    cur.execute('SELECT * FROM productos WHERE id = %s', (product_id,))
    product = cur.fetchone()
    conn.close()
    return dict(product) if product else None


# --- FUNCIONES DE CLOUDINARY (Subida y Eliminación) ---
def subir_imagen_a_cloudinary(file, public_id_prefix=None):
    if file and file.filename:
        try:
            upload_result = cloudinary.uploader.upload(
                file, 
                folder=CLOUDINARY_FOLDER,
                resource_type="image",
                # Optimización para buena calidad y formato automático
                quality="auto:good",
                fetch_format="auto"
            )
            return upload_result.get('secure_url')
        
        except Exception as e:
            print(f"Error al subir la imagen a Cloudinary: {e}")
            flash(f"Error al subir la imagen a la nube: {e}", 'error')
            return None
    return None

def eliminar_imagen_de_cloudinary(imagen_url):
    if not imagen_url:
        return True
    
    try:
        # Extraer el ID público de la URL segura de Cloudinary
        path_segments = imagen_url.split('/')
        file_name_with_ext = path_segments[-1]
        public_id = os.path.splitext(file_name_with_ext)[0]
        # Cloudinary necesita el folder + public_id
        cloudinary_id = f"{CLOUDINARY_FOLDER}/{public_id}"
        
        result = cloudinary.uploader.destroy(cloudinary_id)
        
        if result.get('result') in ('ok', 'not found'):
            print(f"Imagen {cloudinary_id} eliminada de Cloudinary (o no existía).")
            return True
        else:
            print(f"Error desconocido al eliminar de Cloudinary: {result}")
            return False
            
    except Exception as e:
        print(f"Error al intentar eliminar de Cloudinary: {e}")
        return False


# La ruta de índice (muestra todo el catálogo público)
@app.route('/')
def index():
    conn = get_db_connection()
    # USANDO DICTCURSOR
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    # Ejecuta SQL y obtiene todos los productos, ordenados del más nuevo al más antiguo
    cur.execute('SELECT * FROM productos ORDER BY id DESC')
    productos = cur.fetchall()
    conn.close()
    return render_template('index.html', productos=[dict(row) for row in productos])


# --- RUTA DE ADMINISTRACIÓN (Paginación y Búsqueda) ---
@app.route('/admin')
def admin():
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
    
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('q', '').strip() 
    offset = (page - 1) * PRODUCTS_PER_PAGE

    # --- Lógica de Búsqueda y Paginación para PostgreSQL ---
    where_clause = ""
    query_params = []
    order_clause = " ORDER BY id DESC"
    
    if search_query:
        # PostgreSQL Búsqueda con ILIKE (Case Insensitive LIKE)
        where_clause = " WHERE nombre ILIKE %s OR codigo ILIKE %s"
        # Se agregan comodines % en Python, NO en el SQL
        like_pattern = f'%{search_query}%'
        query_params = [like_pattern, like_pattern]
        order_clause = " ORDER BY nombre ASC" 
        
    # Consulta para obtener el total de productos (con o sin filtro)
    count_query = 'SELECT COUNT(id) FROM productos' + where_clause
    cur.execute(count_query, query_params)
    total_productos = cur.fetchone()[0]
    
    # Consulta para obtener los productos de la página actual
    productos_query = 'SELECT * FROM productos' + where_clause + order_clause + ' LIMIT %s OFFSET %s'
    productos_params = query_params + [PRODUCTS_PER_PAGE, offset]
    
    cur.execute(productos_query, productos_params)
    productos = cur.fetchall()
    conn.close()

    # 3. Calcular la información de paginación
    total_pages = math.ceil(total_productos / PRODUCTS_PER_PAGE)
    
    # Lógica para mostrar un rango de 5 páginas (2 antes, página actual, 2 después)
    current_page = page
    start_page = max(1, current_page - 2)
    end_page = min(total_pages, current_page + 2)
    
    # Ajustar el rango si estamos cerca del inicio o el final
    if current_page <= 3:
        end_page = min(total_pages, 5)
    if current_page >= total_pages - 2:
        start_page = max(1, total_pages - 4)

    pages = range(start_page, end_page + 1)

    # Convertir psycopg2.Row a dicts para compatibilidad con render_template
    productos_dicts = [dict(row) for row in productos]

    return render_template(
        'admin.html', 
        productos=productos_dicts, 
        total_productos=total_productos, 
        total_pages=total_pages,
        current_page=current_page,
        search_query=search_query,
        pages=pages
    )

# --- RUTA DE CREACIÓN: add_product ---
@app.route('/add_product', methods=('POST',))
def add_product():
    if request.method == "POST":
        # Asegurarse de que el código no tenga espacios extra
        codigo = request.form['codigo'].strip() 
        nombre = request.form['nombre'].strip()
        descripcion = request.form.get('descripcion', '').strip() 
        precio = request.form['precio']
        imagen_url = None

        file = request.files.get('image')
        if file and file.filename != '':
            imagen_url = subir_imagen_a_cloudinary(file)

        if not nombre or not precio or not codigo:
            flash('El código, nombre y precio son requeridos.', 'error')
        else:
            conn = get_db_connection()
            cur = conn.cursor()

            try:
                # Se usa %s para psycopg2
                cur.execute('INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url) VALUES (%s, %s, %s, %s, %s)',
                            (codigo, nombre, descripcion, precio, imagen_url))
                conn.commit()
                flash('El producto se ha agregado correctamente.', 'success')
            except psycopg2.errors.UniqueViolation as e:
                conn.rollback() 
                flash(f'Error: El código de producto "{codigo}" ya existe en la base de datos. Por favor, use uno diferente.', 'error')
                
                # Si falla por código duplicado, la imagen ya subida a Cloudinary DEBE eliminarse
                if imagen_url and not eliminar_imagen_de_cloudinary(imagen_url):
                    print(f"ADVERTENCIA: Falló la limpieza de la imagen {imagen_url} después de un error de duplicado de código.")
                    
                conn.close()
                return redirect(url_for('admin'))

            except Exception as e:
                conn.rollback()
                flash(f'Error al insertar el producto: {e}', 'error')
                
            finally:
                conn.close()

    return redirect(url_for('admin'))

# --- RUTA DE EDICIÓN: edit_product ---
@app.route('/edit_product/<product_id>', methods=('GET', 'POST'))
def edit_product(product_id):
    
    try:
        product_id = int(product_id)
    except (ValueError, TypeError):
        flash('ID de producto inválido.', 'error')
        return redirect(url_for('admin'))

    # Se busca el producto original
    product = get_product(product_id)

    if product is None:
        flash('Producto no encontrado.', 'error')
        return redirect(url_for('admin'))

    if request.method == "POST":
        codigo = request.form['codigo'].strip()
        nombre = request.form['nombre'].strip()
        descripcion = request.form['descripcion'].strip()
        precio = request.form['precio']
        imagen_url = product['imagen_url'] # URL antigua
        new_imagen_url = None

        file = request.files.get('image')
        # Lógica de subida de nueva imagen
        if file and file.filename != '':
            new_imagen_url = subir_imagen_a_cloudinary(file)
            
            if new_imagen_url:
                # 1. Eliminar la imagen antigua de Cloudinary
                if product['imagen_url']:
                    eliminar_imagen_de_cloudinary(product['imagen_url'])
                    
                imagen_url = new_imagen_url # Usar la nueva URL
        
        conn = get_db_connection()
        cur = conn.cursor()
        try:
            # 2. Actualizar la DB
            cur.execute('UPDATE productos SET codigo = %s, nombre = %s, descripcion = %s, precio = %s, imagen_url = %s WHERE id = %s',
                        (codigo, nombre, descripcion, precio, imagen_url, product_id))
            conn.commit()
            flash('El producto se ha actualizado correctamente.', 'success')
            
        except psycopg2.errors.UniqueViolation as e:
            conn.rollback()
            flash(f'Error: El código de producto "{codigo}" ya existe en otro producto.', 'error')
            # Si falla la DB, eliminar la imagen recién subida
            if new_imagen_url and new_imagen_url != product['imagen_url']:
                eliminar_imagen_de_cloudinary(new_imagen_url)
            conn.close()
            return redirect(url_for('edit_product', product_id=product_id))

        except Exception as e:
            conn.rollback()
            flash(f'Error al actualizar el producto: {e}', 'error')
            
        finally:
            conn.close()
            
        return redirect(url_for('admin'))

    # Para el método GET, retornamos el template
    return render_template('edit_product.html', producto=product)

# --- RUTA DE ELIMINACIÓN: delete_product ---
@app.route('/delete_product/<int:product_id>', methods=('POST',))
def delete_product(product_id):
    conn = get_db_connection()
    product = get_product(product_id)
    cur = conn.cursor()
    
    if product:
        try:
            # 1. Eliminar imagen de Cloudinary
            if product.get('imagen_url'):
                eliminar_imagen_de_cloudinary(product['imagen_url'])
            
            # 2. Eliminar registro de DB
            cur.execute('DELETE FROM productos WHERE id = %s', (product_id,))
            conn.commit()
            flash('El producto se ha eliminado correctamente.', 'success')
            
        except Exception as e:
            conn.rollback()
            flash(f'Error al eliminar el producto: {e}', 'error')
            
        finally:
            conn.close()
            
    # Redirigir, manteniendo los parámetros de búsqueda y paginación
    return redirect(url_for('admin', q=request.args.get('q'), page=request.args.get('page')))


# --- RUTA DE ELIMINACIÓN DE IMAGEN: delete_image ---
@app.route('/delete_image/<int:product_id>', methods=['POST'])
def delete_image(product_id):
    conn = get_db_connection()
    product = get_product(product_id)
    cur = conn.cursor()
    
    if product and product.get('imagen_url'):
        try:
            # 1. Eliminar la imagen de Cloudinary
            if eliminar_imagen_de_cloudinary(product['imagen_url']):
                # 2. Actualizar la DB para eliminar la URL
                cur.execute('UPDATE productos SET imagen_url = NULL WHERE id = %s', (product_id,))
                conn.commit()
                flash('La foto del producto ha sido eliminada.', 'success')
            else:
                flash('Error al eliminar la foto de la nube (Cloudinary).', 'error')

        except Exception as e:
            conn.rollback()
            flash(f'Error al procesar la actualización de la foto: {e}', 'error')

        finally:
            conn.close()
    else:
        flash('No se encontró una foto para eliminar.', 'error')
    
    return redirect(url_for('edit_product', product_id=product_id))

# --- RUTA DE IMPORTACIÓN (Lógica de CSV y DB) ---
@app.route('/importar_productos', methods=('POST',))
def importar_productos():
    if 'csv_file' not in request.files:
        flash('No se ha seleccionado ningún archivo.', 'error')
        return redirect(url_for('admin'))

    file = request.files['csv_file']

    if file.filename == '' or not file.filename.lower().endswith('.csv'):
        flash('Archivo inválido. Por favor, sube un archivo CSV.', 'error')
        return redirect(url_for('admin'))

    conn = None
    try:
        # Decodificación inicial para manejar archivos de Excel/Windows (cp1252 o utf-8)
        
        # Leemos el archivo completo en memoria con una decodificación tolerante
        file_content_raw = file.stream.read().decode('utf-8', errors='replace')
        # Si la decodificación falla, intentamos cp1252 que es común en Excel de Windows
        if '\ufffd' in file_content_raw:
             file.stream.seek(0)
             file_content_raw = file.stream.read().decode('cp1252', errors='replace')

        # Usamos StringIO para simular un archivo en disco, necesario para copy_from
        string_io = io.StringIO()
        
        # --- 1. PREPARACIÓN Y LIMPIEZA DE DATOS ---
        
        # Usamos el dialecto 'excel' para el lector de CSV. Esto ayuda a manejar comillas.
        reader = csv.reader(io.StringIO(file_content_raw), dialect='excel')
        
        # Saltar el encabezado y limpiar el BOM si existe en la primera columna
        header_row = next(reader, None)
        if header_row and header_row[0].startswith('\ufeff'): # \ufeff es el BOM
            header_row[0] = header_row[0].lstrip('\ufeff')

        total_filas = 0
        productos_limpios_csv = io.StringIO()
        
        # USAMOS QUOTE_MINIMAL PARA QUE csv_writer ENCIERRE LOS CAMPOS CON COMAS EN COMILLAS
        # Esto asegura que el CSV limpio cumpla con el formato estándar que PostgreSQL espera.
        csv_writer = csv.writer(productos_limpios_csv, quoting=csv.QUOTE_MINIMAL)

        # Definimos los índices de las columnas que nos interesan en tu CSV de 11 columnas
        # ASUMIMOS:
        # Código = Índice 0 (Columna 1)
        # Nombre = Índice 1 (Columna 2)
        # Descripción = Índice 2 (Columna 3)
        # Precio = Índice 3 (Columna 4)
        CODIGO_INDEX = 0
        NOMBRE_INDEX = 1
        DESCRIPCION_INDEX = 2
        PRECIO_INDEX = 3
        MIN_COLUMNS_REQUIRED = 4 # Requerimos al menos 4 columnas

        for row in reader:
            total_filas += 1
            # 
            # FIX CRÍTICO: Asegurarnos de que la fila tenga al menos las columnas requeridas (4)
            # 
            if len(row) < MIN_COLUMNS_REQUIRED: 
                print(f"Advertencia de Formato: Salteando fila {total_filas} por no tener las {MIN_COLUMNS_REQUIRED} columnas mínimas.")
                continue 

            try:
                # Extracción y Limpieza de datos (usando los índices definidos)
                codigo = row[CODIGO_INDEX].strip().lstrip('\ufeff') 
                nombre = row[NOMBRE_INDEX].strip() 
                descripcion = row[DESCRIPCION_INDEX].strip()
                
                # Limpieza de precio: elimina símbolos y convierte a float.
                precio_str = row[PRECIO_INDEX].strip().replace('$', '').replace('.', '').replace(',', '.') # Limpieza agresiva de formato de moneda
                precio = float(precio_str)
                
                # CREACIÓN DE LA COLUMNA FALTANTE: Se establece la URL de la imagen como vacía
                imagen_url = '' 
                
                # Escribimos la fila limpia al nuevo stream CSV en el orden de la DB:
                # (codigo, nombre, descripcion, precio, imagen_url)
                csv_writer.writerow([codigo, nombre, descripcion, precio, imagen_url])
                
            except ValueError as ve:
                print(f"Advertencia de Valor: Salteando fila {total_filas} con código '{row[CODIGO_INDEX]}' por error de precio o formato: {ve}")
                continue 
            except Exception as e:
                print(f"Error desconocido en fila {total_filas} con código '{row[CODIGO_INDEX]}': {e}")
                continue
        
        # Si no hay datos, retornar
        if productos_limpios_csv.tell() == 0:
            flash('El archivo CSV no contenía productos válidos para importar.', 'warning')
            return redirect(url_for('admin'))

        # Mover el cursor al inicio del stream para que copy_from pueda leerlo
        productos_limpios_csv.seek(0)
            
        # --- 2. CONEXIÓN Y COPY FROM (El método más rápido) ---
        conn = get_db_connection()
        cur = conn.cursor()

        # Usar una tabla temporal para la ingesta
        cur.execute("""
            CREATE TEMPORARY TABLE temp_productos (
                codigo TEXT,
                nombre TEXT,
                descripcion TEXT,
                precio REAL,
                imagen_url TEXT
            ) ON COMMIT DROP;
        """)

        # Usar copy_from para la inserción masiva a la tabla temporal
        cur.copy_from(
            productos_limpios_csv, 
            'temp_productos', 
            # FIX: Usar format='csv' para indicar a PostgreSQL el formato.
            format='csv', 
            # IMPORTANTE: Aquí se especifica que son 5 columnas
            columns=('codigo', 'nombre', 'descripcion', 'precio', 'imagen_url'),
            sep=',', 
            # FIX CRÍTICO: Quitar el argumento 'quote', no es aceptado por psycopg2.copy_from
        )

        # Transferir datos de la tabla temporal a la tabla principal (INSERT ON CONFLICT)
        cur.execute("""
            INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url)
            SELECT codigo, nombre, descripcion, precio, imagen_url
            FROM temp_productos
            ON CONFLICT (codigo) DO NOTHING;
        """)

        # Calcular totales (esta es una estimación aproximada)
        total_productos_csv = total_filas 
        total_insertados = cur.rowcount
        total_duplicados = total_productos_csv - total_insertados

        conn.commit() 
        
        flash(f'¡Importación finalizada! Productos añadidos: {total_insertados}. Se detectaron {total_duplicados} filas con código duplicado o error de formato/columnas.', 'success')
        
    except Exception as e:
        if 'conn' in locals() and conn:
            conn.rollback()
        # Mensaje de error más detallado en caso de fallo de formato
        flash(f'Error grave durante la importación. Verifique que las columnas 1, 2, 3 y 4 de su CSV contienen Código, Nombre, Descripción y Precio. Detalles: {e}', 'error')
        print(f'Error de importación general: {e}')
    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin'))
    
# --- RUTA DE SUBIDA RÁPIDA: upload_product_image ---
@app.route('/upload_image/<int:product_id>', methods=['POST'])
def upload_product_image(product_id):
    search_query = request.args.get('q', '')
    current_page = request.args.get('page', 1)
    # Definir la redirección de administración para evitar repetición
    redirect_to_admin = redirect(url_for('admin', q=search_query, page=current_page))
    
    conn = get_db_connection()
    product = get_product(product_id)
    cur = conn.cursor()
    
    if not product:
        conn.close()
        flash('Producto no encontrado.', 'error')
        return redirect_to_admin

    if 'image_file' not in request.files or request.files['image_file'].filename == '':
        conn.close()
        flash('No se seleccionó ningún archivo.', 'error')
        return redirect_to_admin

    file = request.files['image_file']

    # --- Lógica principal de subida ---
    new_imagen_url = subir_imagen_a_cloudinary(file) 
    
    if new_imagen_url:
        try:
            # 1. Eliminar la imagen antigua de Cloudinary
            if product.get('imagen_url'):
                eliminar_imagen_de_cloudinary(product['imagen_url'])
            
            # 2. Actualizar la base de datos con la nueva URL de Cloudinary
            cur.execute("UPDATE productos SET imagen_url = %s WHERE id = %s", (new_imagen_url, product_id))
            conn.commit()
            conn.close()
            
            flash('¡Foto del producto actualizada con éxito en la nube!', 'success')
        
        except Exception as e:
            conn.rollback()
            conn.close()
            flash(f'Error al procesar la actualización de la foto: {e}', 'error')
            # Si la DB falla, intentamos limpiar la imagen que acabamos de subir a Cloudinary
            eliminar_imagen_de_cloudinary(new_imagen_url)
    else:
        conn.close()
        flash('Error al procesar la imagen subida (Cloudinary).', 'error')

    # Redirigir manteniendo los parámetros
    return redirect_to_admin


if __name__ == "__main__":
    app.run(debug=True)
