from flask import Flask, render_template, request, url_for, flash, redirect
# Se elimina: from werkzeug.utils import secure_filename
# Se elimina: from PIL import Image, ImageOps
import sqlite3
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
DATABASE = 'catalogo.db'
# Las siguientes variables de carpeta local ya NO son necesarias para la subida
# UPLOAD_FOLDER = 'static/uploads' 
# UPLOAD_FOLDER_NAME = 'uploads' 

app = Flask(__name__)
# Se elimina la configuración de UPLOAD_FOLDER
app.secret_key = os.environ.get('SECRET_KEY', 'una_clave_secreta_fuerte') # Usa SECRET_KEY del .env

# Define cuántos productos quieres mostrar por página
PRODUCTS_PER_PAGE = 20

# --- CONFIGURACIÓN DE CLOUDINARY ---
# Utiliza las credenciales cargadas desde el .env
cloudinary.config(
    cloud_name=os.environ.get('CLOUD_NAME'),
    api_key=os.environ.get('API_KEY'),
    api_secret=os.environ.get('API_SECRET'),
    secure=True
)
# Nombre de la carpeta en Cloudinary donde se subirán las imágenes
CLOUDINARY_FOLDER = "catalogo-ferreteria-nea"

# Se elimina la verificación de os.path.exists(app.config['UPLOAD_FOLDER'])

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    c = conn.cursor()

    # 1. Crear la tabla si no existe
    conn.execute('''
        CREATE TABLE IF NOT EXISTS productos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT,
            nombre TEXT NOT NULL,
            descripcion TEXT,
            precio REAL NOT NULL,
            # imagen_url ahora guardará la URL COMPLETA de Cloudinary
            imagen_url TEXT
        );
    ''')
    
    # 2. Verificar y añadir la columna 'codigo' si falta
    c.execute("PRAGMA table_info(productos)")
    column_info = [col[1] for col in c.fetchall()]
    if 'codigo' not in column_info:
        conn.execute("ALTER TABLE productos ADD COLUMN codigo TEXT")
    
    # 3. CREAR UN ÍNDICE ÚNICO para la columna 'codigo'
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_codigo ON productos (codigo)')
    
    # --- BLOQUE FTS (se mantiene intacto) ---
    conn.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS productos_fts USING fts5(
            codigo,
            nombre,
            content='productos', 
            content_rowid='id'
        );
    ''')
    
    # Triggers (se mantienen intactos)
    conn.execute('''
        CREATE TRIGGER IF NOT EXISTS productos_ai AFTER INSERT ON productos BEGIN
          INSERT INTO productos_fts(rowid, codigo, nombre) VALUES (new.id, new.codigo, new.nombre);
        END;
    ''')
    conn.execute('''
        CREATE TRIGGER IF NOT EXISTS productos_ad AFTER DELETE ON productos BEGIN
          INSERT INTO productos_fts(productos_fts, rowid, codigo, nombre) VALUES('delete', old.id, old.codigo, old.nombre);
        END;
    ''')
    conn.execute('''
        CREATE TRIGGER IF NOT EXISTS productos_au AFTER UPDATE ON productos BEGIN
          INSERT INTO productos_fts(productos_fts, rowid, codigo, nombre) VALUES('delete', old.id, old.codigo, old.nombre);
          INSERT INTO productos_fts(rowid, codigo, nombre) VALUES (new.id, new.codigo, new.nombre);
        END;
    ''')
        
    conn.commit()
    conn.close()

# Asegurar que la DB se inicializa con el schema correcto
with app.app_context():
    init_db()

# Nueva función auxiliar para buscar productos por código
def get_product_by_codigo(codigo):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE codigo = ?', (codigo,)).fetchone()
    conn.close()
    return product

# --- FUNCIÓN REESCRITA PARA CLOUDINARY ---
def subir_imagen_a_cloudinary(file, public_id_prefix=None):
    """
    Sube un archivo de imagen a Cloudinary.
    Retorna la URL pública segura o None en caso de error.
    """
    if file and file.filename:
        try:
            # Subida directa a Cloudinary. Cloudinary maneja la optimización y formatos.
            upload_result = cloudinary.uploader.upload(
                file, 
                folder=CLOUDINARY_FOLDER,
                resource_type="image",
                # Opciones de transformación/optimización para el despliegue
                quality="auto:good",
                fetch_format="auto"
            )
            # Retorna la URL pública segura (HTTPS)
            return upload_result.get('secure_url')
        
        except Exception as e:
            print(f"Error al subir la imagen a Cloudinary: {e}")
            flash(f"Error al subir la imagen a la nube: {e}", 'error')
            return None
    return None

# --- FUNCIÓN REESCRITA PARA CLOUDINARY ---
def eliminar_imagen_de_cloudinary(imagen_url):
    """
    Elimina una imagen de Cloudinary usando su URL.
    Retorna True si la eliminación fue exitosa o si la URL es nula.
    """
    if not imagen_url:
        return True
    
    try:
        # Extraer el Public ID de la URL
        # Ejemplo: /v123456789/catalogo-ferreteria-nea/nombre_unico.jpg
        path_segments = imagen_url.split('/')
        
        # El Public ID es el nombre del archivo sin extensión, prefijado por la carpeta
        # Nombre_archivo.ext -> nombre_archivo
        file_name_with_ext = path_segments[-1]
        public_id = os.path.splitext(file_name_with_ext)[0]
        
        # Crear el ID completo para la API: 'carpeta/public_id'
        cloudinary_id = f"{CLOUDINARY_FOLDER}/{public_id}"
        
        # Eliminar el recurso
        result = cloudinary.uploader.destroy(cloudinary_id)
        
        if result.get('result') == 'ok':
            print(f"Imagen {cloudinary_id} eliminada de Cloudinary.")
            return True
        elif result.get('result') == 'not found':
             # Ya estaba eliminada, no es un error critico
            print(f"Advertencia: Imagen {cloudinary_id} no encontrada en Cloudinary (posiblemente ya eliminada).")
            return True
        else:
            print(f"Error desconocido al eliminar de Cloudinary: {result}")
            return False
            
    except Exception as e:
        print(f"Error al intentar eliminar de Cloudinary: {e}")
        return False
        
def get_product(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE id = ?', (product_id,)).fetchone()
    conn.close()
    return product

# La ruta de índice no cambia (muestra todo el catálogo público)
@app.route('/')
def index():
    conn = get_db_connection()
    productos = conn.execute('SELECT * FROM productos').fetchall()
    conn.close()
    return render_template('index.html', productos=productos)


# --- RUTA DE ADMINISTRACIÓN (se mantiene intacta) ---
@app.route('/admin')
def admin():
    conn = get_db_connection()
    
    # 1. Obtener parámetros de búsqueda y paginación
    page = request.args.get('page', 1, type=int)
    search_query = request.args.get('q', '').strip() 
    offset = (page - 1) * PRODUCTS_PER_PAGE

    # 2. Construir la consulta SQL
    where_clause = ""
    query_params = []
    order_clause = " ORDER BY id DESC"
    
    if search_query:
        # **Lógica de BÚSQUEDA DIFUSA FTS MEJORADA**
        clean_query = ''.join(c for c in search_query if c.isalnum() or c.isspace()).lower()
        tokens = clean_query.split()
        
        fts_parts = []
        for token in tokens:
            fts_parts.append(f'{token}*') 
            if token.endswith('s') and len(token) > 2:
                fts_parts.append(f'{token.rstrip("s")}*')

        fts_pattern = " OR ".join(fts_parts)
        
        where_clause = " JOIN productos_fts ON productos.id = productos_fts.rowid WHERE productos_fts MATCH ? "
        query_params = [fts_pattern]
        order_clause = " ORDER BY rank" 
        
    # Consulta para obtener el total de productos (con o sin filtro)
    count_query = 'SELECT COUNT(productos.id) FROM productos' + where_clause
    total_productos = conn.execute(count_query, query_params).fetchone()[0]
    
    # Consulta para obtener los productos de la página actual
    productos_query = 'SELECT productos.* FROM productos' + where_clause + order_clause + ' LIMIT ? OFFSET ?'
    productos_params = query_params + [PRODUCTS_PER_PAGE, offset]
    
    productos = conn.execute(productos_query, productos_params).fetchall()
    conn.close()

    # 3. Calcular la información de paginación
    total_pages = math.ceil(total_productos / PRODUCTS_PER_PAGE)
    
    current_page = page
    start_page = max(1, current_page - 2)
    end_page = min(total_pages, current_page + 2)
    
    pages = range(start_page, end_page + 1)

    return render_template(
        'admin.html', 
        productos=productos, 
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
            # Se usa la nueva función de subida a Cloudinary
            imagen_url = subir_imagen_a_cloudinary(file)

        if not nombre or not precio:
            flash('El nombre y el precio son requeridos.', 'error')
        else:
            conn = get_db_connection()
            # 1. Verificar si el código ya existe
            if get_product_by_codigo(codigo) and codigo:
                flash(f'Error: El código de producto "{codigo}" ya existe en la base de datos.', 'error')
                # Si falla por código duplicado, la imagen ya subida a Cloudinary DEBE eliminarse
                if imagen_url and not eliminar_imagen_de_cloudinary(imagen_url):
                     print(f"ADVERTENCIA: Falló la limpieza de la imagen {imagen_url} después de un error de duplicado de código.")
                conn.close()
                return redirect(url_for('admin'))
            
            try:
                # Se inserta la URL de Cloudinary
                conn.execute('INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url) VALUES (?, ?, ?, ?, ?)',
                             (codigo, nombre, descripcion, precio, imagen_url))
                conn.commit()
                flash('El producto se ha agregado correctamente.', 'success')
            except sqlite3.IntegrityError:
                flash(f'Error: El código de producto "{codigo}" ya existe (Integridad DB).', 'error')
                # Si falla por integridad, también se elimina la imagen de Cloudinary
                if imagen_url and not eliminar_imagen_de_cloudinary(imagen_url):
                    print(f"ADVERTENCIA: Falló la limpieza de la imagen {imagen_url} después de un error de integridad DB.")
            finally:
                conn.close()

    return redirect(url_for('admin'))

# --- RUTA MODIFICADA: edit_product ---
@app.route('/edit_product/<product_id>', methods=('GET', 'POST'))
def edit_product(product_id):
    conn = get_db_connection()
    
    try:
        product_id = int(product_id)
    except (ValueError, TypeError):
        flash('ID de producto inválido.', 'error')
        conn.close()
        return redirect(url_for('admin'))

    product = conn.execute('SELECT * FROM productos WHERE id = ?', (product_id,)).fetchone()

    if product is None:
        conn.close()
        flash('Producto no encontrado.', 'error')
        return redirect(url_for('admin'))

    if request.method == "POST":
        codigo = request.form['codigo']
        nombre = request.form['nombre']
        descripcion = request.form['descripcion']
        precio = request.form['precio']
        imagen_url = product['imagen_url'] # URL antigua

        file = request.files.get('image')
        if file and file.filename != '':
            # 1. Subir la nueva imagen a Cloudinary
            new_imagen_url = subir_imagen_a_cloudinary(file)
            
            if new_imagen_url:
                # 2. Eliminar la imagen antigua de Cloudinary
                if product['imagen_url']:
                    eliminar_imagen_de_cloudinary(product['imagen_url'])
                    
                imagen_url = new_imagen_url # Usar la nueva URL
        
        # Validación: si el código ha cambiado y el nuevo código ya existe en OTRO producto
        existing_product = get_product_by_codigo(codigo)
        if existing_product and existing_product['id'] != product_id:
            flash(f'Error: El código de producto "{codigo}" ya existe en otro producto.', 'error')
            conn.close()
            # Si la subida fue exitosa pero falla la DB, eliminar la imagen recién subida
            if new_imagen_url and new_imagen_url != product['imagen_url']:
                 eliminar_imagen_de_cloudinary(new_imagen_url)
            return redirect(url_for('edit_product', product_id=product_id))

        try:
            # Se actualiza la URL (será la antigua si no se subió una nueva, o la de Cloudinary)
            conn.execute('UPDATE productos SET codigo = ?, nombre = ?, descripcion = ?, precio = ?, imagen_url = ? WHERE id = ?',
                         (codigo, nombre, descripcion, precio, imagen_url, product_id))
            conn.commit()
            flash('El producto se ha actualizado correctamente.', 'success')
        except sqlite3.IntegrityError:
            flash(f'Error: El código de producto "{codigo}" ya existe en otro producto (Integridad DB).', 'error')
            # Si falla la DB, eliminar la imagen recién subida
            if new_imagen_url and new_imagen_url != product['imagen_url']:
                 eliminar_imagen_de_cloudinary(new_imagen_url)
            conn.close()
            return redirect(url_for('edit_product', product_id=product_id))

        conn.close()
        return redirect(url_for('admin'))

    conn.close()
    return render_template('edit_product.html', producto=product)

# --- RUTA MODIFICADA: delete_product ---
@app.route('/delete_product/<int:product_id>', methods=('POST',))
def delete_product(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE id = ?', (product_id,)).fetchone()
    if product:
        if product['imagen_url']:
            # Lógica para eliminar de Cloudinary
            eliminar_imagen_de_cloudinary(product['imagen_url'])
        
        conn.execute('DELETE FROM productos WHERE id = ?', (product_id,))
        conn.commit()
        flash('El producto se ha eliminado correctamente.', 'success')
    conn.close()
    return redirect(url_for('admin', q=request.args.get('q'), page=request.args.get('page')))


# --- RUTA MODIFICADA: delete_image ---
@app.route('/delete_image/<int:product_id>', methods=['POST'])
def delete_image(product_id):
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE id = ?', (product_id,)).fetchone()
    if product and product['imagen_url']:
        
        # 1. Eliminar la imagen de Cloudinary
        if eliminar_imagen_de_cloudinary(product['imagen_url']):
            # 2. Actualizar la DB para eliminar la URL
            conn.execute('UPDATE productos SET imagen_url = NULL WHERE id = ?', (product_id,))
            conn.commit()
            flash('La foto del producto ha sido eliminada.', 'success')
        else:
            flash('Error al eliminar la foto de la nube (Cloudinary).', 'error')

    else:
        flash('No se encontró una foto para eliminar.', 'error')
    
    conn.close()
    return redirect(url_for('edit_product', product_id=product_id))

# --- RUTA MODIFICADA: reiniciar_db ---
@app.route('/reiniciar_db')
def reiniciar_db():
    if os.path.exists(DATABASE):
        try:
            # Las imágenes subidas a Cloudinary no se eliminan aquí
            # Se elimina el archivo de la base de datos local
            os.remove(DATABASE)
            flash('Base de datos local eliminada exitosamente. Se recreará al iniciar el servidor.', 'success')
        except OSError as e:
            flash(f"Error al eliminar la base de datos: {e}", 'error')
    else:
        flash("La base de datos no existe para eliminarla.", 'error')
    
    # Después de eliminar, la función init_db() se encargará de crearla de nuevo
    with app.app_context():
        init_db()
    
    return redirect(url_for('admin'))

# --- RUTA DE IMPORTACIÓN (Se mantiene intacta ya que solo usa la DB) ---
@app.route('/importar_productos', methods=('POST',))
def importar_productos():
    # ... código de importación ... (se mantiene intacto)
    if 'csv_file' not in request.files:
        flash('No se ha seleccionado ningún archivo.', 'error')
        return redirect(url_for('admin'))

    file = request.files['csv_file']

    if file.filename == '' or not file.filename.lower().endswith('.csv'):
        flash('Archivo inválido. Por favor, sube un archivo CSV.', 'error')
        return redirect(url_for('admin'))

    try:
        conn = get_db_connection()
        
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
                
                if get_product_by_codigo(codigo):
                    total_duplicados += 1
                    continue
                
                nombre = row[1].strip() 
                descripcion = '' 

                precio_str = row[3].strip().replace('$', '').replace(',', '')
                precio = float(precio_str)
                imagen_url = None # Por defecto, no hay imagen al importar CSV

                conn.execute('INSERT INTO productos (codigo, nombre, descripcion, precio, imagen_url) VALUES (?, ?, ?, ?, ?)',
                              (codigo, nombre, descripcion, precio, imagen_url))
                total_importados += 1
                
            except ValueError:
                continue 
            except sqlite3.IntegrityError:
                total_duplicados += 1
                continue 
        
        conn.commit()
        conn.close()
        flash(f'¡Importación finalizada! Productos añadidos: {total_importados}. Productos duplicados omitidos: {total_duplicados}.', 'success')
        
    except Exception as e:
        flash(f'Error durante la importación: {e}', 'error')
        print(f'Error de importación: {e}')

    return redirect(url_for('admin'))
    
# --- RUTA MODIFICADA: upload_product_image (Subida rápida) ---
@app.route('/upload_image/<int:product_id>', methods=['POST'])
def upload_product_image(product_id):
    """Maneja la subida rápida de una imagen a Cloudinary para un producto específico, eliminando la anterior."""
    
    search_query = request.args.get('q', '')
    current_page = request.args.get('page', 1)
    redirect_to_admin = redirect(url_for('admin', q=search_query, page=current_page))
    
    conn = get_db_connection()
    product = conn.execute('SELECT * FROM productos WHERE id = ?', (product_id,)).fetchone()
    
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
            if product['imagen_url']:
                eliminar_imagen_de_cloudinary(product['imagen_url'])
            
            # 2. Actualizar la base de datos con la nueva URL de Cloudinary
            conn.execute("UPDATE productos SET imagen_url = ? WHERE id = ?", (new_imagen_url, product_id))
            conn.commit()
            conn.close()
            
            flash('¡Foto del producto actualizada con éxito en la nube!', 'success')
        
        except Exception as e:
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
