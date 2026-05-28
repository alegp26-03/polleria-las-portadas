"""
TPV - Terminal Punto de Venta (Version BLINDADA v3)
Servidor Flask + Frontend tactil integrado

Protecciones incluidas:
  - Escrituras atomicas (tempfile + os.replace)
  - Backups automaticos rotativos (hasta 3 copias)
  - Auto-recuperacion si el archivo se corrompe
  - Backup diario al arrancar
  - Try/except en TODAS las rutas
  - Validacion de datos en todas las entradas
  - Log de errores con timestamps
  - Compatible con CSV con o sin columna 'id'

Novedades v3:
  - Pedidos pendientes por telefono
  - Busqueda de pedidos por telefono
  - Cobro de pedidos pendientes
"""
import csv
import os
import io
import json
import shutil
import tempfile
import logging
import traceback
import sys
from datetime import datetime
from flask import Flask, request, jsonify, Response

if getattr(sys, 'frozen', False):
    # Si se está ejecutando como un .exe empaquetado
    BASE_DIR = os.path.dirname(sys.executable)
else:
    # Si se está ejecutando como un script normal de Python
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# =============================================================================
#  CONFIGURACION
# =============================================================================
app = Flask(__name__)
CSV_PROD = os.path.join(BASE_DIR, 'productos.csv')
CSV_VENTAS = os.path.join(BASE_DIR, 'ventas.csv')
CSV_PEDIDOS = os.path.join(BASE_DIR, 'pedidos_pendientes.csv')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
MAX_BACKUPS_ROTATIVOS = 3

# =============================================================================
#  SISTEMA DE LOGGING
# =============================================================================
LOG_FILE = os.path.join(BASE_DIR, 'tpv_errores.log')
logging.basicConfig(
    filename=LOG_FILE,
    level=logging.ERROR,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    encoding='utf-8'
)
logger = logging.getLogger('TPV')

info_handler = logging.FileHandler(
    os.path.join(BASE_DIR, 'tpv_actividad.log'), encoding='utf-8'
)
info_handler.setLevel(logging.INFO)
info_handler.setFormatter(logging.Formatter(
    '%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S'
))
logger.addHandler(info_handler)
logger.setLevel(logging.INFO)


# =============================================================================
#  FUNCIONES DE SEGURIDAD - Escritura atomica y backups
# =============================================================================

def escritura_atomica(filepath, contenido_csv, fieldnames):
    """Escribe un CSV de forma ATOMICA con tempfile + os.replace."""
    directorio = os.path.dirname(filepath)
    tmp_path = None
    try:
        fd, tmp_path = tempfile.mkstemp(suffix='.tmp', dir=directorio)
        with os.fdopen(fd, 'w', newline='', encoding='utf-8') as tmp_file:
            writer = csv.DictWriter(tmp_file, fieldnames=fieldnames)
            writer.writeheader()
            for row in contenido_csv:
                writer.writerow(row)
        os.replace(tmp_path, filepath)
        return True
    except Exception as e:
        logger.error(f"ERROR en escritura atomica de {filepath}: {e}")
        logger.error(traceback.format_exc())
        try:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
        except:
            pass
        return False


def hacer_backup_rotativo(filepath):
    """Crea backups rotativos: _backup_1, _backup_2, _backup_3."""
    if not os.path.exists(filepath):
        return
    try:
        nombre_base = os.path.splitext(filepath)[0]
        extension = os.path.splitext(filepath)[1]
        for i in range(MAX_BACKUPS_ROTATIVOS, 1, -1):
            origen = f"{nombre_base}_backup_{i-1}{extension}"
            destino = f"{nombre_base}_backup_{i}{extension}"
            if os.path.exists(origen):
                shutil.copy2(origen, destino)
        backup_1 = f"{nombre_base}_backup_1{extension}"
        shutil.copy2(filepath, backup_1)
        logger.info(f"Backup rotativo creado: {backup_1}")
    except Exception as e:
        logger.error(f"ERROR creando backup rotativo de {filepath}: {e}")


def hacer_backup_diario(filepath):
    """Backup diario en carpeta backups/."""
    if not os.path.exists(filepath):
        return
    try:
        os.makedirs(BACKUP_DIR, exist_ok=True)
        nombre = os.path.basename(filepath)
        nombre_base = os.path.splitext(nombre)[0]
        extension = os.path.splitext(nombre)[1]
        fecha = datetime.now().strftime('%Y-%m-%d')
        backup_path = os.path.join(BACKUP_DIR, f"{nombre_base}_{fecha}{extension}")
        if not os.path.exists(backup_path):
            shutil.copy2(filepath, backup_path)
            logger.info(f"Backup diario creado: {backup_path}")
        limpiar_backups_antiguos(BACKUP_DIR, dias=30)
    except Exception as e:
        logger.error(f"ERROR creando backup diario: {e}")


def limpiar_backups_antiguos(directorio, dias=30):
    """Elimina backups con mas de X dias."""
    try:
        ahora = datetime.now().timestamp()
        for archivo in os.listdir(directorio):
            ruta = os.path.join(directorio, archivo)
            if os.path.isfile(ruta):
                edad_dias = (ahora - os.path.getmtime(ruta)) / 86400
                if edad_dias > dias:
                    os.remove(ruta)
                    logger.info(f"Backup antiguo eliminado: {archivo}")
    except Exception as e:
        logger.error(f"ERROR limpiando backups antiguos: {e}")


# =============================================================================
#  LECTURA DE PRODUCTOS - Con auto-recuperacion
# =============================================================================

def leer_productos():
    """Lee productos.csv con multiples niveles de proteccion."""
    resultado = _intentar_leer_csv(CSV_PROD)
    if resultado is not None:
        return resultado

    logger.error("ARCHIVO PRINCIPAL CORRUPTO - Intentando recuperar desde backups...")

    nombre_base = os.path.splitext(CSV_PROD)[0]
    extension = os.path.splitext(CSV_PROD)[1]

    for i in range(1, MAX_BACKUPS_ROTATIVOS + 1):
        backup_path = f"{nombre_base}_backup_{i}{extension}"
        resultado = _intentar_leer_csv(backup_path)
        if resultado is not None:
            logger.info(f"RECUPERADO desde {backup_path} ({len(resultado)} productos)")
            try:
                shutil.copy2(backup_path, CSV_PROD)
                logger.info(f"Archivo principal restaurado desde {backup_path}")
            except Exception as e:
                logger.error(f"No se pudo restaurar el archivo principal: {e}")
            return resultado

    if os.path.exists(BACKUP_DIR):
        backups_diarios = sorted(
            [f for f in os.listdir(BACKUP_DIR) if f.startswith('productos_')],
            reverse=True
        )
        for backup_nombre in backups_diarios:
            backup_path = os.path.join(BACKUP_DIR, backup_nombre)
            resultado = _intentar_leer_csv(backup_path)
            if resultado is not None:
                logger.info(f"RECUPERADO desde backup diario {backup_nombre}")
                try:
                    shutil.copy2(backup_path, CSV_PROD)
                except:
                    pass
                return resultado

    logger.error("TODOS LOS BACKUPS FALLARON - Devolviendo lista vacia")
    return []


def _intentar_leer_csv(filepath):
    """Lee CSV de productos. Compatible con/sin columna 'id'."""
    if not os.path.exists(filepath):
        return None
    try:
        if os.path.getsize(filepath) == 0:
            return None
        productos = []
        with open(filepath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return None
            if 'nombre' not in reader.fieldnames or 'precio' not in reader.fieldnames:
                logger.error(f"CSV {filepath} le faltan columnas basicas: {reader.fieldnames}")
                return None
            auto_id = 1
            for row in reader:
                try:
                    if 'id' in row and row['id']:
                        row['id'] = int(row['id'])
                        if row['id'] >= auto_id:
                            auto_id = row['id'] + 1
                    else:
                        row['id'] = auto_id
                        auto_id += 1
                    row['precio'] = float(row['precio'])
                    if not row.get('nombre', '').strip():
                        continue
                    if not row.get('categoria', '').strip():
                        row['categoria'] = 'Sin categoria'
                    productos.append(row)
                except (ValueError, KeyError) as e:
                    logger.error(f"Fila corrupta ignorada en {filepath}: {row} -> {e}")
                    continue
        return productos
    except Exception as e:
        logger.error(f"Error leyendo {filepath}: {e}")
        return None


# =============================================================================
#  GUARDAR PRODUCTOS
# =============================================================================

def guardar_productos(productos):
    """Guarda productos de forma SEGURA con backup + escritura atomica."""
    hacer_backup_rotativo(CSV_PROD)
    fieldnames = ['id', 'nombre', 'precio', 'categoria']
    productos_limpios = []
    for p in productos:
        productos_limpios.append({
            'id': p.get('id', 0),
            'nombre': p.get('nombre', ''),
            'precio': p.get('precio', 0),
            'categoria': p.get('categoria', 'Sin categoria')
        })
    exito = escritura_atomica(CSV_PROD, productos_limpios, fieldnames)
    if exito:
        logger.info(f"Productos guardados correctamente ({len(productos_limpios)} items)")
    else:
        logger.error("FALLO al guardar productos")
    return exito


def siguiente_id(productos):
    """Calcula el siguiente ID disponible."""
    if not productos:
        return 1
    return max(p['id'] for p in productos) + 1


# =============================================================================
#  VENTAS
# =============================================================================

def registrar_venta(data):
    """Registra una venta de forma segura."""
    try:
        es_nuevo = not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0
        if not es_nuevo:
            hacer_backup_diario_ventas()
        with open(CSV_VENTAS, 'a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            if es_nuevo:
                writer.writerow(['timestamp', 'items_json', 'total',
                                 'pagado_efectivo', 'pagado_tarjeta',
                                 'cambio', 'metodo_pago'])
            writer.writerow([
                data.get('timestamp', ''),
                json.dumps(data.get('items', []), ensure_ascii=False),
                data.get('total', '0.00'),
                data.get('pagado_efectivo', '0.00'),
                data.get('pagado_tarjeta', '0.00'),
                data.get('cambio', '0.00'),
                data.get('metodo_pago', 'efectivo')
            ])
        logger.info(f"Venta registrada: {data.get('total', '?')} EUR ({data.get('metodo_pago', '?')})")
        return True
    except Exception as e:
        logger.error(f"ERROR registrando venta: {e}")
        logger.error(traceback.format_exc())
        return False


def hacer_backup_diario_ventas():
    """Backup diario del archivo de ventas."""
    try:
        if not os.path.exists(CSV_VENTAS):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        fecha = datetime.now().strftime('%Y-%m-%d')
        backup_path = os.path.join(BACKUP_DIR, f"ventas_{fecha}.csv")
        if not os.path.exists(backup_path):
            shutil.copy2(CSV_VENTAS, backup_path)
    except Exception as e:
        logger.error(f"ERROR en backup diario de ventas: {e}")


def leer_ventas_hoy():
    """Lee las ventas del dia de hoy."""
    ventas = []
    try:
        if not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0:
            return ventas
        hoy = datetime.now().strftime('%Y-%m-%d')
        with open(CSV_VENTAS, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = row.get('timestamp', '')
                    if ts and ts.startswith(hoy):
                        try:
                            row['items'] = json.loads(row.get('items_json', '[]'))
                        except:
                            row['items'] = []
                        row.pop('items_json', None)
                        ventas.append(row)
                except Exception as e:
                    logger.error(f"Fila de venta corrupta ignorada: {e}")
                    continue
    except Exception as e:
        logger.error(f"ERROR leyendo ventas: {e}")
    return ventas


# =============================================================================
#  PEDIDOS PENDIENTES (TELEFONO) - NUEVO en v3
# =============================================================================

PEDIDOS_FIELDNAMES = ['id', 'telefono', 'nombre_cliente', 'items_json', 'total', 'timestamp', 'estado']


def leer_pedidos():
    """Lee todos los pedidos pendientes del CSV."""
    pedidos = []
    try:
        if not os.path.exists(CSV_PEDIDOS) or os.path.getsize(CSV_PEDIDOS) == 0:
            return pedidos
        with open(CSV_PEDIDOS, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                return pedidos
            for row in reader:
                try:
                    row['id'] = int(row['id'])
                    row['total'] = float(row['total'])
                    try:
                        row['items'] = json.loads(row.get('items_json', '[]'))
                    except:
                        row['items'] = []
                    pedidos.append(row)
                except (ValueError, KeyError) as e:
                    logger.error(f"Pedido corrupto ignorado: {row} -> {e}")
                    continue
    except Exception as e:
        logger.error(f"ERROR leyendo pedidos: {e}")
    return pedidos


def guardar_pedidos(pedidos):
    """Guarda pedidos de forma segura con backup + escritura atomica."""
    hacer_backup_rotativo(CSV_PEDIDOS)
    pedidos_csv = []
    for p in pedidos:
        items_json = p.get('items_json', '')
        if not items_json and 'items' in p:
            items_json = json.dumps(p['items'], ensure_ascii=False)
        pedidos_csv.append({
            'id': p.get('id', 0),
            'telefono': p.get('telefono', ''),
            'nombre_cliente': p.get('nombre_cliente', ''),
            'items_json': items_json,
            'total': p.get('total', 0),
            'timestamp': p.get('timestamp', ''),
            'estado': p.get('estado', 'pendiente')
        })
    exito = escritura_atomica(CSV_PEDIDOS, pedidos_csv, PEDIDOS_FIELDNAMES)
    if exito:
        logger.info(f"Pedidos guardados correctamente ({len(pedidos_csv)} pedidos)")
    else:
        logger.error("FALLO al guardar pedidos")
    return exito


def siguiente_id_pedido(pedidos):
    """Calcula el siguiente ID de pedido."""
    if not pedidos:
        return 1
    return max(p['id'] for p in pedidos) + 1


def hacer_backup_diario_pedidos():
    """Backup diario del archivo de pedidos."""
    try:
        if not os.path.exists(CSV_PEDIDOS):
            return
        os.makedirs(BACKUP_DIR, exist_ok=True)
        fecha = datetime.now().strftime('%Y-%m-%d')
        backup_path = os.path.join(BACKUP_DIR, f"pedidos_pendientes_{fecha}.csv")
        if not os.path.exists(backup_path):
            shutil.copy2(CSV_PEDIDOS, backup_path)
    except Exception as e:
        logger.error(f"ERROR en backup diario de pedidos: {e}")


# =============================================================================
#  VALIDACION DE DATOS
# =============================================================================

def validar_producto(data):
    """Valida datos de un producto. Devuelve (True, datos) o (False, error)."""
    errores = []
    nombre = data.get('nombre', '').strip() if data.get('nombre') else ''
    if not nombre:
        errores.append('El nombre no puede estar vacio')
    elif len(nombre) > 200:
        errores.append('El nombre es demasiado largo (max 200 caracteres)')

    try:
        precio = float(data.get('precio', 0))
        if precio < 0:
            errores.append('El precio no puede ser negativo')
        elif precio > 99999:
            errores.append('El precio es demasiado alto')
        precio = round(precio, 2)
    except (ValueError, TypeError):
        errores.append('El precio no es un numero valido')
        precio = 0

    categoria = data.get('categoria', '').strip() if data.get('categoria') else ''
    if not categoria:
        errores.append('La categoria no puede estar vacia')

    if errores:
        return False, '; '.join(errores)
    return True, {'nombre': nombre, 'precio': precio, 'categoria': categoria}


# =============================================================================
#  RUTAS API - PRODUCTOS
# =============================================================================

@app.route('/api/productos', methods=['GET'])
def api_get_productos():
    try:
        return jsonify(leer_productos())
    except Exception as e:
        logger.error(f"ERROR en GET /api/productos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/productos', methods=['POST'])
def api_add_producto():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        valido, resultado = validar_producto(data)
        if not valido:
            return jsonify({'error': resultado}), 400
        productos = leer_productos()
        nuevo = {
            'id': siguiente_id(productos),
            'nombre': resultado['nombre'],
            'precio': resultado['precio'],
            'categoria': resultado['categoria']
        }
        productos.append(nuevo)
        if guardar_productos(productos):
            return jsonify(nuevo), 201
        else:
            return jsonify({'error': 'Error al guardar'}), 500
    except Exception as e:
        logger.error(f"ERROR en POST /api/productos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/productos/<int:pid>', methods=['PUT'])
def api_update_producto(pid):
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        productos = leer_productos()
        for p in productos:
            if p['id'] == pid:
                if 'nombre' in data:
                    nombre = str(data['nombre']).strip()
                    if nombre:
                        p['nombre'] = nombre
                if 'precio' in data:
                    try:
                        precio = round(float(data['precio']), 2)
                        if 0 <= precio <= 99999:
                            p['precio'] = precio
                    except (ValueError, TypeError):
                        return jsonify({'error': 'Precio no valido'}), 400
                if 'categoria' in data:
                    categoria = str(data['categoria']).strip()
                    if categoria:
                        p['categoria'] = categoria
                if guardar_productos(productos):
                    return jsonify(p)
                else:
                    return jsonify({'error': 'Error al guardar'}), 500
        return jsonify({'error': 'Producto no encontrado'}), 404
    except Exception as e:
        logger.error(f"ERROR en PUT /api/productos/{pid}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/productos/<int:pid>', methods=['DELETE'])
def api_delete_producto(pid):
    try:
        productos = leer_productos()
        cantidad_antes = len(productos)
        productos = [p for p in productos if p['id'] != pid]
        if len(productos) == cantidad_antes:
            return jsonify({'error': 'Producto no encontrado'}), 404
        if guardar_productos(productos):
            return jsonify({'ok': True})
        else:
            return jsonify({'error': 'Error al guardar'}), 500
    except Exception as e:
        logger.error(f"ERROR en DELETE /api/productos/{pid}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


# =============================================================================
#  RUTAS API - VENTAS
# =============================================================================

@app.route('/api/venta', methods=['POST'])
def api_registrar_venta():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        if registrar_venta(data):
            return jsonify({'ok': True, 'timestamp': data['timestamp']}), 201
        else:
            return jsonify({'error': 'Error al registrar venta'}), 500
    except Exception as e:
        logger.error(f"ERROR en POST /api/venta: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/ventas', methods=['GET'])
def api_get_ventas():
    try:
        return jsonify(leer_ventas_hoy())
    except Exception as e:
        logger.error(f"ERROR en GET /api/ventas: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


# =============================================================================
#  RUTAS API - PEDIDOS PENDIENTES (TELEFONO) - NUEVO en v3
# =============================================================================

@app.route('/api/pedido', methods=['POST'])
def api_crear_pedido():
    """Crea un pedido pendiente (pedido por telefono, se paga al recoger)."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400

        telefono = str(data.get('telefono', '')).strip()
        if not telefono:
            return jsonify({'error': 'El telefono es obligatorio'}), 400

        nombre_cliente = str(data.get('nombre_cliente', '')).strip()
        items = data.get('items', [])
        if not items:
            return jsonify({'error': 'El pedido no tiene productos'}), 400

        try:
            total = round(float(data.get('total', 0)), 2)
        except (ValueError, TypeError):
            return jsonify({'error': 'Total no valido'}), 400

        pedidos = leer_pedidos()
        nuevo = {
            'id': siguiente_id_pedido(pedidos),
            'telefono': telefono,
            'nombre_cliente': nombre_cliente,
            'items_json': json.dumps(items, ensure_ascii=False),
            'items': items,
            'total': total,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'estado': 'pendiente'
        }
        pedidos.append(nuevo)

        if guardar_pedidos(pedidos):
            logger.info(f"Pedido #{nuevo['id']} creado: tel={telefono}, total={total}")
            return jsonify({
                'ok': True,
                'pedido': {
                    'id': nuevo['id'],
                    'telefono': nuevo['telefono'],
                    'nombre_cliente': nuevo['nombre_cliente'],
                    'items': nuevo['items'],
                    'total': nuevo['total'],
                    'timestamp': nuevo['timestamp'],
                    'estado': nuevo['estado']
                }
            }), 201
        else:
            return jsonify({'error': 'Error al guardar pedido'}), 500

    except Exception as e:
        logger.error(f"ERROR en POST /api/pedido: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/pedidos', methods=['GET'])
def api_get_pedidos():
    """Devuelve todos los pedidos pendientes (estado=pendiente)."""
    try:
        pedidos = leer_pedidos()
        pendientes = [p for p in pedidos if p.get('estado') == 'pendiente']
        # Limpiar items_json del resultado (ya tenemos items parseado)
        for p in pendientes:
            p.pop('items_json', None)
        return jsonify(pendientes)
    except Exception as e:
        logger.error(f"ERROR en GET /api/pedidos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/pedidos/buscar', methods=['GET'])
def api_buscar_pedidos():
    """Busca pedidos pendientes por telefono (busqueda parcial)."""
    try:
        tel = request.args.get('tel', '').strip()
        if not tel:
            return jsonify({'error': 'Introduce un telefono para buscar'}), 400

        pedidos = leer_pedidos()
        resultados = [
            p for p in pedidos
            if p.get('estado') == 'pendiente' and tel in str(p.get('telefono', ''))
        ]
        for p in resultados:
            p.pop('items_json', None)
        return jsonify(resultados)

    except Exception as e:
        logger.error(f"ERROR en GET /api/pedidos/buscar: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/pedido/<int:pid>/cobrar', methods=['POST'])
def api_cobrar_pedido(pid):
    """
    Cobra un pedido pendiente:
    1. Registra la venta en ventas.csv
    2. Marca el pedido como 'recogido'
    3. Devuelve los datos para el ticket
    """
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos de pago'}), 400

        pedidos = leer_pedidos()
        pedido = None
        for p in pedidos:
            if p['id'] == pid and p.get('estado') == 'pendiente':
                pedido = p
                break

        if not pedido:
            return jsonify({'error': 'Pedido no encontrado o ya cobrado'}), 404

        # Datos de pago
        metodo = data.get('metodo_pago', 'efectivo')
        pagado_efectivo = float(data.get('pagado_efectivo', 0))
        pagado_tarjeta = float(data.get('pagado_tarjeta', 0))
        cambio = float(data.get('cambio', 0))
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Actualizamos con la última versión del carrito justo antes de cobrar
        items_finales = data.get('items', pedido.get('items', []))
        total_final = data.get('total', pedido['total'])

        # 1. Registrar la venta
        venta_data = {
            'timestamp': timestamp,
            'items': items_finales,
            'total': str(total_final),
            'pagado_efectivo': str(round(pagado_efectivo, 2)),
            'pagado_tarjeta': str(round(pagado_tarjeta, 2)),
            'cambio': str(round(cambio, 2)),
            'metodo_pago': metodo
        }

        if not registrar_venta(venta_data):
            return jsonify({'error': 'Error al registrar la venta'}), 500

        # 2. Marcar pedido como recogido
        pedido['estado'] = 'recogido'
        if not guardar_pedidos(pedidos):
            logger.error(f"Venta registrada pero pedido #{pid} no se pudo marcar como recogido")

        logger.info(f"Pedido #{pid} cobrado: {pedido['total']} EUR ({metodo})")

        # 3. Devolver datos para el ticket
        return jsonify({
            'ok': True,
            'timestamp': timestamp,
            'pedido': {
                'id': pedido['id'],
                'telefono': pedido.get('telefono', ''),
                'nombre_cliente': pedido.get('nombre_cliente', ''),
                'items': pedido.get('items', []),
                'total': pedido['total'],
                'estado': 'recogido'
            }
        }), 200

    except Exception as e:
        logger.error(f"ERROR en POST /api/pedido/{pid}/cobrar: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/pedido/<int:pid>', methods=['DELETE'])
def api_cancelar_pedido(pid):
    """Cancela (elimina) un pedido pendiente."""
    try:
        pedidos = leer_pedidos()
        cantidad_antes = len(pedidos)
        pedidos = [p for p in pedidos if not (p['id'] == pid and p.get('estado') == 'pendiente')]

        if len(pedidos) == cantidad_antes:
            return jsonify({'error': 'Pedido no encontrado o ya cobrado'}), 404

        if guardar_pedidos(pedidos):
            logger.info(f"Pedido #{pid} cancelado")
            return jsonify({'ok': True})
        else:
            return jsonify({'error': 'Error al guardar'}), 500

    except Exception as e:
        logger.error(f"ERROR en DELETE /api/pedido/{pid}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500
    
@app.route('/api/pedido/<int:pid>', methods=['PUT'])
def api_update_pedido(pid):
    """Actualiza un pedido pendiente (añadir/quitar platos)."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400

        pedidos = leer_pedidos()
        pedido_actualizado = None
        
        for p in pedidos:
            if p['id'] == pid and p.get('estado') == 'pendiente':
                if 'telefono' in data:
                    p['telefono'] = str(data['telefono']).strip()
                if 'nombre_cliente' in data:
                    p['nombre_cliente'] = str(data['nombre_cliente']).strip()
                if 'items' in data:
                    p['items'] = data['items']
                    p['items_json'] = json.dumps(data['items'], ensure_ascii=False)
                if 'total' in data:
                    p['total'] = float(data['total'])
                
                pedido_actualizado = p
                break

        if not pedido_actualizado:
            return jsonify({'error': 'Pedido no encontrado o ya cerrado'}), 404

        if guardar_pedidos(pedidos):
            return jsonify({'ok': True, 'pedido': pedido_actualizado}), 200
        else:
            return jsonify({'error': 'Error al guardar'}), 500

    except Exception as e:
        logger.error(f"ERROR en PUT /api/pedido/{pid}: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


# =============================================================================
#  RUTA PRINCIPAL
# =============================================================================

@app.route('/')
def index():
    """Sirve la pagina principal."""
    try:
        html_path = os.path.join(BASE_DIR, 'index.html')
        if os.path.exists(html_path):
            with open(html_path, 'r', encoding='utf-8') as f:
                return Response(f.read(), mimetype='text/html')
        else:
            logger.error(f"index.html NO encontrado en {BASE_DIR}")
            return Response(
                '<h1>Error: index.html no encontrado</h1>'
                '<p>Asegurate de que el archivo index.html esta en la misma '
                'carpeta que tpv.py: ' + BASE_DIR + '</p>',
                mimetype='text/html'
            ), 404
    except Exception as e:
        logger.error(f"ERROR sirviendo index.html: {e}")
        return Response('<h1>Error interno del servidor</h1>', mimetype='text/html'), 500


# =============================================================================
#  MIGRACION AUTOMATICA
# =============================================================================

def migrar_csv_si_necesario():
    """Si el CSV no tiene columna 'id', lo migra automaticamente."""
    if not os.path.exists(CSV_PROD):
        return
    try:
        with open(CSV_PROD, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            if reader.fieldnames and 'id' not in reader.fieldnames:
                logger.info("CSV sin columna 'id' detectado - Migrando...")
                productos = leer_productos()
                if productos:
                    guardar_productos(productos)
                    logger.info(f"Migracion completada: {len(productos)} productos con IDs asignados")
                    print(f'  [MIGRADO] productos.csv actualizado con columna id ({len(productos)} productos)')
    except Exception as e:
        logger.error(f"ERROR en migracion: {e}")


# =============================================================================
#  ARRANQUE SEGURO
# =============================================================================

def arranque_seguro():
    """Comprobaciones al iniciar la aplicacion."""
    print('')
    print('  [SEGURIDAD] Realizando comprobaciones de arranque...')

    os.makedirs(BACKUP_DIR, exist_ok=True)
    print(f'  [OK] Carpeta de backups: {BACKUP_DIR}')

    migrar_csv_si_necesario()

    if os.path.exists(CSV_PROD):
        productos = leer_productos()
        print(f'  [OK] productos.csv leido correctamente ({len(productos)} productos)')
        hacer_backup_diario(CSV_PROD)
    else:
        print('  [INFO] productos.csv no existe todavia (se creara al anadir productos)')

    if os.path.exists(CSV_VENTAS):
        hacer_backup_diario_ventas()
        print('  [OK] ventas.csv existe - backup diario creado')
    else:
        print('  [INFO] ventas.csv no existe todavia')

    if os.path.exists(CSV_PEDIDOS):
        hacer_backup_diario_pedidos()
        pedidos = leer_pedidos()
        pendientes = len([p for p in pedidos if p.get('estado') == 'pendiente'])
        print(f'  [OK] pedidos_pendientes.csv existe ({pendientes} pedidos pendientes)')
    else:
        print('  [INFO] pedidos_pendientes.csv no existe todavia')

    html_path = os.path.join(BASE_DIR, 'index.html')
    if os.path.exists(html_path):
        print('  [OK] index.html encontrado')
    else:
        print(f'  [AVISO] index.html NO encontrado en {BASE_DIR}')
        print('          Coloca el archivo index.html en esa carpeta')

    print(f'  [OK] Log de errores: {LOG_FILE}')
    print('')


# =============================================================================
#  MAIN
# =============================================================================

if __name__ == '__main__':
    print('=' * 54)
    print('  Polleria Las Portadas - TPV')
    print('  Version BLINDADA v3 - Pedidos por telefono')
    print('  Abre http://localhost:5000 en tu navegador')
    print('=' * 54)

    arranque_seguro()

    print('  [LISTO] Servidor iniciado en http://localhost:5000')
    print('  [LISTO] Pulsa CTRL+C para detener')
    print('=' * 54)
    print('')

    app.run(host='0.0.0.0', port=5000, debug=False)
