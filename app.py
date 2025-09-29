from flask import Flask, render_template, request, url_for, flash, redirect
# NO USAMOS sqlite3 en Render
# Se importa el driver de PostgreSQL
import psycopg2 
from psycopg2 import sql 
import os
import secrets
import csv
import io
import math 
# Librería para cargar variables de entorno
from dotenv import load_dotenv

# Importaciones de Cloudinary para manejo de archivos
import cloudinary
import cloudinary.uploader
import cloudinary.api

# Cargar las variables de entorno del archivo .env
load_dotenv()

# --- CONFIGURACIÓN DE FLASK ---
# La variable DATABASE ya no apunta a un archivo, se usa la variable de entorno
# DATABASE = 'catalogo.db' 
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_fuerte')

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
            
        # Conexión a PostgreSQL
        conn = psycopg2.connect(db_url)
        # Permite acceder a las columnas por nombre (como si fuera sqlite3.Row)
        conn.row_factory = psycopg2.extras.DictCursor 
        return conn
        
    except Exception as e:
        print(f"Error al conectar con la base de datos PostgreSQL: {e}")
        # En caso de error, la aplicación no debería seguir, por eso levantamos el error.
        raise

def init_db():
    """
    Inicializa el esquema de la base de datos.
    ADVERTENCIA: PostgreSQL no usa AUTOINCREMENT ni PRAGMA.
    """
    conn = get_db_connection()
    
    # 1. Crear la tabla 'productos' si no existe
    # Se reemplaza INTEGER PRIMARY KEY AUTOINCREMENT por SERIAL PRIMARY KEY
    # Se elimina el comentario '#' dentro del SQL
    conn.execute(sql.SQL("""
        CREATE TABLE IF NOT EXISTS productos (
            id SERIAL PRIMARY KEY,
            codigo TEXT UNIQUE,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            imagen_url TEXT
        );
    """))
    
    # 2. CREAR UN ÍNDICE ÚNICO para la columna 'codigo' 
    # (aunque SERIAL PRIMARY KEY ya lo garantiza, se mantiene para consistencia y códigos externos)
    # Se usa UNIQUE en la definición de la tabla para el código.

    # 3. Crear la tabla FTS (Aquí debemos usar el módulo pg_trgm de Postgres, no fts5 de SQLite)
    # Render ya tiene PostgreSQL configurado, pero no podemos crear tablas VIRTUALES FTS5.
    # Por ahora, para simplificar el despliegue y evitar errores, ELIMINAMOS el bloque FTS y sus triggers.
    # La búsqueda FTS se gestionará directamente en Python con LIKE o con un índice en el futuro.
    
    conn.commit()
    conn.close()

# Asegurar que la DB se inicializa con el schema correcto
with app.app_context():
    init_db()

# Nueva función auxiliar para buscar productos por código (psycopg2)
def get_product_by_codigo(codigo):
    conn = get_db_connection()
    # Se usa la sintaxis de %s para psycopg2 en lugar de ?
    product = conn.execute('SELECT * FROM productos WHERE codigo = %s', (codigo,)).fetchone()
    conn.close()
    # Si el producto existe, retornamos un objeto que se comporta como Row (DictCursor)
    return dict(product) if product else None

# Función auxiliar para obtener producto por ID (psycopg2)
def get_product(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE id = %s', (product_id,)).fetchone()
    conn.close()
    return dict(product) if product else None


# --- FUNCIÓN REESCRITA PARA CLOUDINARY (se mantiene intacta) ---
def subir_imagen_a_cloudinary(file, public_id_prefix=None):
    # ... (Se mantiene intacta la lógica de Cloudinary) ...
    if file and file.filename:
        try:
            upload_result = cloudinary.uploader.upload(
                file, 
                folder=CLOUDINARY_FOLDER,
                resource_type="image",
                quality="auto:good",
                fetch_format="auto"
            )
            return upload_result.get('secure_url')
        
        except Exception as e:
            print(f"Error al subir la imagen a Cloudinary: {e}")
            flash(f"Error al subir la imagen a la nube: {e}", 'error')
            return None
    return None

# --- FUNCIÓN REESCRITA PARA CLOUDINARY (se mantiene intacta) ---
def eliminar_imagen_de_cloudinary(imagen_url):
    # ... (Se mantiene intacta la lógica de Cloudinary) ...
    if not imagen_url:
        return True
    
    try:
        path_segments = imagen_url.split('/')
        file_name_with_ext = path_segments[-1]
        public_id = os.path.splitext(file_name_with_ext)[0]
        cloudinary_id = f"{CLOUDINARY_FOLDER}/{public_id}"
        
        result = cloudinary.uploader.destroy(cloudinary_id)
        
        if result.get('result') == 'ok':
            print(f"Imagen {cloudinary_id} eliminada de Cloudinary.")
            return True
        elif result.get('result') == 'not found':
            print(f"Advertencia: Imagen {cloudinary_id} no encontrada en Cloudinary.")
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
    # Ejecuta SQL y obtiene todos los productos
    conn.execute('SELECT * FROM productos ORDER BY id DESC')
    productos = conn.fetchall()
    conn.close()
    return render_template('index.html', productos=productos)


# --- RUTA DE ADMINISTRACIÓN (MODIFICADA: Búsqueda simple con LIKE) ---
@app.route('/admin')
def admin():
    conn = get_db_connection()
    
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
    conn.execute(count_query, query_params)
    total_productos = conn.fetchone()[0]
    
    # Consulta para obtener los productos de la página actual
    productos_query = 'SELECT * FROM productos' + where_clause + order_clause + ' LIMIT %s OFFSET %s'
    productos_params = query_params + [PRODUCTS_PER_PAGE, offset]
    
    conn.execute(productos_query, productos_params)
    productos = conn.fetchall()
    conn.close()

    # 3. Calcular la información de paginación
    total_pages = math.ceil(total_productos / PRODUCTS_PER_PAGE)
    
    current_page = page
    start_page = max(1, current_page - 2)
    end_page = min(total_pages, current_page + 2)
    
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

# --- RUTA MODIFICADA: add_product ---
@app.route('/add_product', methods=('POST',))
def add_product():
    if request.method == "POST":
        codigo = request.form['codigo'] 
        nombre = request.form['nombre']
        descripcion = request.form.get('descripcion', '') 
        precio = request.form['precio']
        imagen_url = None

        file = request.files.get('image')
        if file and file.filename != '':
            imagen_url = subir_imagen_a_cloudinary(file)

        if not nombre or not precio:
            flash('El nombre y el precio son requeridos.', 'error')
        else:
            conn = get_db_connection()
            
            # La verificación de código duplicado ya ocurre implícitamente en la DB
            # por el UNIQUE INDEX. Lo dejamos en un TRY/EXCEPT.

            try:
                # Se usa %s para psycopg2 en lugar de ?
                conn.execute('INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url) VALUES (%s, %s, %s, %s, %s)',
                            (codigo, nombre, descripcion, precio, imagen_url))
                conn.commit()
                flash('El producto se ha agregado correctamente.', 'success')
            except psycopg2.errors.UniqueViolation as e:
                # Maneja el error de código duplicado (UniqueViolation)
                conn.rollback() # Deshace la transacción
                flash(f'Error: El código de producto "{codigo}" ya existe en la base de datos.', 'error')
                
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

# --- RUTA MODIFICADA: edit_product ---
@app.route('/edit_product/<product_id>', methods=('GET', 'POST'))
def edit_product(product_id):
    
    try:
        product_id = int(product_id)
    except (ValueError, TypeError):
        flash('ID de producto inválido.', 'error')
        return redirect(url_for('admin'))

    product = get_product(product_id)

    if product is None:
        flash('Producto no encontrado.', 'error')
        return redirect(url_for('admin'))

    if request.method == "POST":
        codigo = request.form['codigo']
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        precio = request.form['precio']
        imagen_url = product['imagen_url'] # URL antigua
        new_imagen_url = None

        file = request.files.get('image')
        if file and file.filename != '':
            new_imagen_url = subir_imagen_a_cloudinary(file)
            
            if new_imagen_url:
                # 1. Eliminar la imagen antigua de Cloudinary
                if product['imagen_url']:
                    eliminar_imagen_de_cloudinary(product['imagen_url'])
                    
                imagen_url = new_imagen_url # Usar la nueva URL
        
        conn = get_db_connection()
        try:
            # 2. Actualizar la DB
            conn.execute('UPDATE productos SET codigo = %s, nombre = %s, descripcion = %s, precio = %s, imagen_url = %s WHERE id = %s',
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

# --- RUTA MODIFICADA: delete_product ---
@app.route('/delete_product/<int:product_id>', methods=('POST',))
def delete_product(product_id):
    conn = get_db_connection()
    product = get_product(product_id)
    
    if product:
        try:
            if product['imagen_url']:
                eliminar_imagen_de_cloudinary(product['imagen_url'])
            
            conn.execute('DELETE FROM productos WHERE id = %s', (product_id,))
            conn.commit()
            flash('El producto se ha eliminado correctamente.', 'success')
            
        except Exception as e:
            conn.rollback()
            flash(f'Error al eliminar el producto: {e}', 'error')
            
        finally:
            conn.close()
            
    return redirect(url_for('admin', q=request.args.get('q'), page=request.args.get('page')))


# --- RUTA MODIFICADA: delete_image ---
@app.route('/delete_image/<int:product_id>', methods=['POST'])
def delete_image(product_id):
    conn = get_db_connection()
    product = get_product(product_id)
    
    if product and product['imagen_url']:
        try:
            # 1. Eliminar la imagen de Cloudinary
            if eliminar_imagen_de_cloudinary(product['imagen_url']):
                # 2. Actualizar la DB para eliminar la URL
                conn.execute('UPDATE productos SET imagen_url = NULL WHERE id = %s', (product_id,))
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

# --- RUTA DE REINICIO DE DB (ELIMINADA) ---
# Hemos eliminado esta ruta porque no podemos eliminar la base de datos de Render.

# --- RUTA DE IMPORTACIÓN (MODIFICADA para psycopg2) ---
@app.route('/importar_productos', methods=('POST',))
def importar_productos():
    # ... código de importación ... 
    if 'csv_file' not in request.files:
        flash('No se ha seleccionado ningún archivo.', 'error')
        return redirect(url_for('admin'))

    file = request.files['csv_file']

    if file.filename == '' or not file.filename.lower().endswith('.csv'):
        flash('Archivo inválido. Por favor, sube un archivo CSV.', 'error')
        return redirect(url_for('admin'))

    conn = None
    try:
        conn = get_db_connection()
        
        # ... (Lógica de CSV se mantiene) ...
        stream = io.TextIOWrapper(file.stream, encoding='cp1252')
        reader = csv.reader(stream)
        
        next(reader, None) 
        
        total_importados = 0
        total_duplicados = 0

        for row in reader:
            if len(row) < 4:
                continue

            try:
                codigo = row[0].strip()
                nombre = row[1].strip() 
                descripcion = '' 

                precio_str = row[3].strip().replace('$', '').replace(',', '')
                precio = float(precio_str)
                imagen_url = None 

                # Insertar en PostgreSQL
                conn.execute('INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url) VALUES (%s, %s, %s, %s, %s)',
                            (codigo, nombre, descripcion, precio, imagen_url))
                total_importados += 1
                
            except ValueError:
                # El precio no es un número válido
                conn.rollback()
                continue
            except psycopg2.errors.UniqueViolation:
                # Código duplicado
                conn.rollback()
                total_duplicados += 1
                continue 
        
        conn.commit()
        flash(f'¡Importación finalizada! Productos añadidos: {total_importados}. Productos duplicados omitidos: {total_duplicados}.', 'success')
        
    except Exception as e:
        flash(f'Error durante la importación: {e}', 'error')
        print(f'Error de importación: {e}')
    finally:
        if conn:
            conn.close()

    return redirect(url_for('admin'))
    
# --- RUTA MODIFICADA: upload_product_image (Subida rápida) ---
@app.route('/upload_image/<int:product_id>', methods=['POST'])
def upload_product_image(product_id):
    # ... (Lógica de subida rápida con las funciones de psycopg2) ...
    search_query = request.args.get('q', '')
    current_page = request.args.get('page', 1)
    redirect_to_admin = redirect(url_for('admin', q=search_query, page=current_page))
    
    conn = get_db_connection()
    product = get_product(product_id)
    
    if not product:
        conn.close()
        flash('Producto no encontrado.', 'error')
        return redirect_to_admin

    if 'image_file' not in request.files:
        conn.close()
        flash('No se seleccionó ningún archivo.', 'error')
        return redirect_to_admin

    file = request.files['image_file']

    if file.filename == '':
        conn.close()
        flash('No se seleccionó ningún archivo.', 'error')
        return redirect_to_admin

    # --- Lógica principal de subida ---
    new_imagen_url = subir_imagen_a_cloudinary(file) 
    
    if new_imagen_url:
        try:
            # 1. Eliminar la imagen antigua de Cloudinary
            if product.get('imagen_url'):
                eliminar_imagen_de_cloudinary(product['imagen_url'])
            
            # 2. Actualizar la base de datos con la nueva URL de Cloudinary
            conn.execute("UPDATE productos SET imagen_url = %s WHERE id = %s", (new_imagen_url, product_id))
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
        flash('Error al procesar la imagen subida.', 'error')

    # Redirigir manteniendo los parámetros
    return redirect_to_admin


if __name__ == "__main__":
    app.run(debug=True)
