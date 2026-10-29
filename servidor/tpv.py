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

Novedades v4:
  - Impresion directa de ticket, comanda y cierre en la impresora termica (ESC/POS)
  - Apertura automatica del cajon en cobros con efectivo y boton manual
  - Entradas/salidas de caja y anulacion de ventas
  - Configuracion de impresora en config_impresora.json
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
import codecs
import textwrap
from datetime import datetime
from flask import Flask, request, jsonify, Response

try:
    import win32print  # pip install pywin32 (necesario para abrir el cajon)
except ImportError:
    win32print = None

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
CSV_CIERRES = os.path.join(BASE_DIR, 'cierres.csv')
CSV_MOVIMIENTOS = os.path.join(BASE_DIR, 'movimientos_caja.csv')
BACKUP_DIR = os.path.join(BASE_DIR, 'backups')
MAX_BACKUPS_ROTATIVOS = 3
CONFIG_IMPRESORA = os.path.join(BASE_DIR, 'config_impresora.json')

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


def asegurar_columnas_csv(filepath, fieldnames):
    """Si el CSV tiene una cabecera antigua, lo reescribe anadiendo las columnas nuevas vacias."""
    try:
        if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
            return True
        with open(filepath, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            actuales = list(reader.fieldnames or [])
            faltan = [c for c in fieldnames if c not in actuales]
            if not faltan:
                return True
            cabecera = actuales + faltan
            filas = [{k: (row.get(k) or '') for k in cabecera} for row in reader]
        hacer_backup_rotativo(filepath)
        if escritura_atomica(filepath, filas, cabecera):
            logger.info(f"Columnas {faltan} anadidas a {filepath}")
            return True
        return False
    except Exception as e:
        logger.error(f"ERROR actualizando columnas de {filepath}: {e}")
        return False


def anadir_fila_csv(filepath, fila, fieldnames):
    """Anade una fila al CSV respetando el orden de su cabecera."""
    es_nuevo = not os.path.exists(filepath) or os.path.getsize(filepath) == 0
    if es_nuevo:
        cabecera = fieldnames
    else:
        if not asegurar_columnas_csv(filepath, fieldnames):
            raise IOError(f"No se pudo actualizar la cabecera de {filepath}")
        with open(filepath, newline='', encoding='utf-8') as f:
            cabecera = next(csv.reader(f))
    with open(filepath, 'a', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=cabecera, extrasaction='ignore')
        if es_nuevo:
            writer.writeheader()
        writer.writerow(fila)


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

# 'anula_a' guarda el timestamp de la venta original en las filas de anulacion
VENTAS_FIELDNAMES = ['timestamp', 'items_json', 'total', 'pagado_efectivo', 'pagado_tarjeta',
                     'cambio', 'metodo_pago', 'anula_a']


def registrar_venta(data):
    """Registra una venta de forma segura."""
    try:
        es_nuevo = not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0
        if not es_nuevo:
            hacer_backup_diario_ventas()
        anadir_fila_csv(CSV_VENTAS, {
            'timestamp': data.get('timestamp', ''),
            'items_json': json.dumps(data.get('items', []), ensure_ascii=False),
            'total': data.get('total', '0.00'),
            'pagado_efectivo': data.get('pagado_efectivo', '0.00'),
            'pagado_tarjeta': data.get('pagado_tarjeta', '0.00'),
            'cambio': data.get('cambio', '0.00'),
            'metodo_pago': data.get('metodo_pago', 'efectivo'),
            'anula_a': data.get('anula_a', '')
        }, VENTAS_FIELDNAMES)
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

CIERRES_FIELDNAMES = ['timestamp', 'fondo_inicial', 'ventas_efectivo', 'ventas_tarjeta',
                      'teorico_efectivo', 'real_efectivo', 'descuadre', 'desglose_json',
                      'entradas', 'salidas']


@app.route('/api/cierre', methods=['POST'])
def api_guardar_cierre():
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        cierre = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'fondo_inicial': data.get('fondo_inicial', '0'),
            'ventas_efectivo': data.get('ventas_efectivo', '0'),
            'ventas_tarjeta': data.get('ventas_tarjeta', '0'),
            'teorico_efectivo': data.get('teorico_efectivo', '0'),
            'real_efectivo': data.get('real_efectivo', '0'),
            'descuadre': data.get('descuadre', '0'),
            'desglose_json': json.dumps(data.get('desglose', {})),
            'entradas': data.get('entradas', '0.00'),
            'salidas': data.get('salidas', '0.00')
        }
        anadir_fila_csv(CSV_CIERRES, cierre, CIERRES_FIELDNAMES)
        logger.info(f"Cierre de caja guardado: descuadre {cierre['descuadre']} EUR")

        impresion = imprimir_cierre(dict(cierre, desglose=data.get('desglose', {}),
                                         total_facturado=data.get('total_facturado', '0')))
        return jsonify({'ok': True, 'impresora': impresion}), 200
    except Exception as e:
        logger.error(f"ERROR en POST /api/cierre: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error guardando cierre'}), 500


# =============================================================================
#  MOVIMIENTOS DE CAJA (entradas y salidas de efectivo que no son ventas)
# =============================================================================

MOVIMIENTOS_FIELDNAMES = ['timestamp', 'tipo', 'importe', 'concepto']


def leer_movimientos_hoy():
    """Lee las entradas/salidas de caja del dia de hoy."""
    movimientos = []
    try:
        if not os.path.exists(CSV_MOVIMIENTOS) or os.path.getsize(CSV_MOVIMIENTOS) == 0:
            return movimientos
        hoy = datetime.now().strftime('%Y-%m-%d')
        with open(CSV_MOVIMIENTOS, newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                if (row.get('timestamp') or '').startswith(hoy):
                    movimientos.append(row)
    except Exception as e:
        logger.error(f"ERROR leyendo movimientos de caja: {e}")
    return movimientos


@app.route('/api/movimientos', methods=['GET'])
def api_get_movimientos():
    try:
        return jsonify(leer_movimientos_hoy())
    except Exception as e:
        logger.error(f"ERROR en GET /api/movimientos: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/movimientos', methods=['POST'])
def api_registrar_movimiento():
    """Registra una entrada o salida de efectivo y abre el cajon."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400

        tipo = str(data.get('tipo', '')).strip().lower()
        if tipo not in ('entrada', 'salida'):
            return jsonify({'error': 'El tipo debe ser entrada o salida'}), 400
        try:
            importe = round(float(data.get('importe', 0)), 2)
        except (ValueError, TypeError):
            return jsonify({'error': 'Importe no valido'}), 400
        if not 0 < importe <= 99999:
            return jsonify({'error': 'El importe debe ser mayor que 0'}), 400
        concepto = str(data.get('concepto', '')).strip()[:200]
        if not concepto:
            return jsonify({'error': 'Indica el concepto del movimiento'}), 400

        movimiento = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'tipo': tipo,
            'importe': f"{importe:.2f}",
            'concepto': concepto
        }
        anadir_fila_csv(CSV_MOVIMIENTOS, movimiento, MOVIMIENTOS_FIELDNAMES)
        logger.info(f"Movimiento de caja: {tipo} {importe:.2f} EUR ({concepto})")

        config = leer_config_impresora()
        impresion = enviar_a_impresora(comando_cajon(config), 'Abrir cajon', config)
        return jsonify({'ok': True, 'movimiento': movimiento, 'impresora': impresion}), 201
    except Exception as e:
        logger.error(f"ERROR en POST /api/movimientos: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500

# =============================================================================
#  PEDIDOS PENDIENTES (TELEFONO) - NUEVO en v3
# =============================================================================

PEDIDOS_FIELDNAMES = ['id', 'telefono', 'nombre_cliente', 'items_json', 'total', 'timestamp', 'hora_recogida', 'estado']


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
            'hora_recogida': p.get('hora_recogida', ''),
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
        if not isinstance(data.get('items'), list) or not data['items']:
            return jsonify({'error': 'La venta no tiene productos'}), 400
        try:
            float(data.get('total'))
        except (ValueError, TypeError):
            return jsonify({'error': 'Total no valido'}), 400
        data['timestamp'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        data.pop('anula_a', None)
        if registrar_venta(data):
            impresion = imprimir_venta(data)
            return jsonify({'ok': True, 'timestamp': data['timestamp'], 'impresora': impresion}), 201
        else:
            return jsonify({'error': 'Error al registrar venta'}), 500
    except Exception as e:
        logger.error(f"ERROR en POST /api/venta: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


def _negar_importe(valor):
    """Cambia el signo de un importe guardado como texto (sin devolver '-0.00')."""
    v = -_a_float(valor)
    return f"{v:.2f}" if abs(v) >= 0.005 else "0.00"


@app.route('/api/venta/anular', methods=['POST'])
def api_anular_venta():
    """Anula una venta de hoy registrando una venta en negativo que la compensa."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        ts = str(data.get('timestamp', '')).strip()
        if not ts:
            return jsonify({'error': 'Falta indicar la venta a anular'}), 400

        ventas = leer_ventas_hoy()
        originales = [v for v in ventas
                      if v.get('timestamp') == ts and v.get('metodo_pago') != 'anulacion']
        if not originales:
            return jsonify({'error': 'Venta no encontrada (solo se pueden anular ventas de hoy)'}), 404
        # Si hubo varias ventas en el mismo segundo, se anulan por orden
        ya_anuladas = sum(1 for v in ventas if v.get('anula_a') == ts)
        if ya_anuladas >= len(originales):
            return jsonify({'error': 'Esta venta ya esta anulada'}), 409
        original = originales[ya_anuladas]

        anulacion = {
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'items': [dict(it, qty=-_a_int(it.get('qty'), 1)) for it in original.get('items') or []],
            'total': _negar_importe(original.get('total')),
            'pagado_efectivo': _negar_importe(original.get('pagado_efectivo')),
            'pagado_tarjeta': _negar_importe(original.get('pagado_tarjeta')),
            'cambio': _negar_importe(original.get('cambio')),
            'metodo_pago': 'anulacion',
            'anula_a': ts
        }
        if not registrar_venta(anulacion):
            return jsonify({'error': 'Error al registrar la anulacion'}), 500
        logger.info(f"Venta {ts} anulada ({original.get('total')} EUR)")

        impresion = imprimir_venta(anulacion)
        return jsonify({'ok': True, 'anulacion': anulacion, 'impresora': impresion}), 201
    except Exception as e:
        logger.error(f"ERROR en POST /api/venta/anular: {e}")
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

        hora_recogida = str(data.get('hora_recogida', '')).strip()

        pedidos = leer_pedidos()
        nuevo = {
            'id': siguiente_id_pedido(pedidos),
            'telefono': telefono,
            'nombre_cliente': nombre_cliente,
            'items_json': json.dumps(items, ensure_ascii=False),
            'items': items,
            'total': total,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'hora_recogida': hora_recogida,
            'estado': 'pendiente'
        }
        pedidos.append(nuevo)

        if guardar_pedidos(pedidos):
            logger.info(f"Pedido #{nuevo['id']} creado: tel={telefono}, total={total}, hora={hora_recogida}")
            return jsonify({
                'ok': True,
                'pedido': {
                    'id': nuevo['id'],
                    'telefono': nuevo['telefono'],
                    'nombre_cliente': nuevo['nombre_cliente'],
                    'items': nuevo['items'],
                    'total': nuevo['total'],
                    'timestamp': nuevo['timestamp'],
                    'hora_recogida': nuevo['hora_recogida'],
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

@app.route('/api/clientes', methods=['GET'])
def api_get_clientes():
    """Devuelve un diccionario de telefono -> nombre_cliente basado en todo el historial."""
    try:
        pedidos = leer_pedidos() # Lee todos, incluso los ya cobrados
        clientes = {}
        for p in pedidos:
            tel = str(p.get('telefono', '')).strip()
            nom = str(p.get('nombre_cliente', '')).strip()
            # Guardamos el más reciente (el último sobreescribe si ha cambiado de nombre)
            if tel and tel.lower() != 'sin teléfono' and nom:
                clientes[tel] = nom
        return jsonify(clientes)
    except Exception as e:
        logger.error(f"ERROR en GET /api/clientes: {e}")
        return jsonify({'error': 'Error interno'}), 500

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

        # 3. Cajon y ticket
        impresion = imprimir_venta(venta_data)

        # 4. Devolver datos para el ticket
        return jsonify({
            'ok': True,
            'timestamp': timestamp,
            'impresora': impresion,
            'pedido': {
                'id': pedido['id'],
                'telefono': pedido.get('telefono', ''),
                'nombre_cliente': pedido.get('nombre_cliente', ''),
                'items': pedido.get('items', []),
                'total': pedido['total'],
                'hora_recogida': pedido.get('hora_recogida', ''),
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
                if 'hora_recogida' in data:
                    p['hora_recogida'] = str(data['hora_recogida']).strip()
                
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
#  IMPRESORA DE TICKETS Y CAJON PORTAMONEDAS (ESC/POS)
# =============================================================================

CONFIG_IMPRESORA_DEFECTO = {
    'impresora': '',
    'pin_cajon': 2,
    'ancho_caracteres': 48,
    'cortar_papel': True,
    'imprimir_ticket_automatico': True,
    'tabla_caracteres': 19,
    'codificacion': 'cp858'
}

TICKET_NOMBRE = 'POLLERIA FREIDURIA LAS PORTADAS'
TICKET_CABECERA = [
    'Av. las Portadas, 2',
    '41700 Dos Hermanas, Sevilla',
    'Tel: 623700918',
    'DNI: 48964063F'
]
IVA = 0.10
PRECIO_EXTRA = 0.80

DESGLOSE_CAJA = [
    ('50e', '50 EUR', 50), ('20e', '20 EUR', 20), ('10e', '10 EUR', 10), ('5e', '5 EUR', 5),
    ('2e', '2 EUR', 2), ('1e', '1 EUR', 1), ('50c', '0,50 EUR', 0.50), ('20c', '0,20 EUR', 0.20),
    ('10c', '0,10 EUR', 0.10), ('5c', '0,05 EUR', 0.05), ('2c', '0,02 EUR', 0.02), ('1c', '0,01 EUR', 0.01)
]

# Bits de PRINTER_STATUS_* de Windows que indican que la impresora no puede imprimir
ESTADOS_IMPRESORA_ERROR = [
    (0x00000001, 'Impresora en pausa'),
    (0x00000002, 'Impresora con error'),
    (0x00000008, 'Atasco de papel'),
    (0x00000010, 'Sin papel'),
    (0x00000080, 'Impresora desconectada'),
    (0x00001000, 'Impresora no disponible'),
    (0x00400000, 'Tapa de la impresora abierta'),
]
PRINTER_ATTRIBUTE_WORK_OFFLINE = 0x00000400


def _a_float(valor, defecto=0.0):
    try:
        return float(valor)
    except (ValueError, TypeError):
        return defecto


def _a_int(valor, defecto=0):
    try:
        return int(float(valor))
    except (ValueError, TypeError):
        return defecto


def formato_euros(valor):
    v = _a_float(valor)
    return f"{v:.2f} €" if abs(v) >= 0.005 else "0.00 €"


def leer_config_impresora():
    """Lee config_impresora.json y anade al archivo las opciones que le falten."""
    config = dict(CONFIG_IMPRESORA_DEFECTO)
    try:
        guardado = {}
        if os.path.exists(CONFIG_IMPRESORA):
            # utf-8-sig: el Bloc de notas puede guardar el archivo con BOM
            with open(CONFIG_IMPRESORA, 'r', encoding='utf-8-sig') as f:
                guardado = json.load(f)
            config.update(guardado)
        if set(CONFIG_IMPRESORA_DEFECTO) - set(guardado):
            with open(CONFIG_IMPRESORA, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(f"ERROR leyendo {CONFIG_IMPRESORA}: {e}")
    return config


class DocumentoESCPOS:
    """Construye un ticket en bytes ESC/POS para la impresora termica."""

    ALINEACION = {'izq': 0, 'centro': 1, 'der': 2}
    TAMANO = {'normal': 0x00, 'alto': 0x01, 'grande': 0x11}

    def __init__(self, config):
        self.ancho = max(_a_int(config.get('ancho_caracteres'), 48), 20)
        self.cortar = bool(config.get('cortar_papel', True))
        self.codificacion = str(config.get('codificacion') or 'cp858')
        try:
            codecs.lookup(self.codificacion)
        except LookupError:
            self.codificacion = 'cp858'
        self.datos = bytearray(b'\x1b\x40')                                     # ESC @: reiniciar
        self.datos += bytes([0x1B, 0x74, _a_int(config.get('tabla_caracteres'), 19) & 0xFF])  # ESC t: tabla

    def _codificar(self, texto):
        # Los caracteres que la impresora no tiene (emojis...) se omiten
        return str(texto).encode(self.codificacion, errors='ignore')

    def _formato(self, alinear, negrita, tamano):
        self.datos += bytes([0x1B, 0x61, self.ALINEACION[alinear]])   # ESC a
        self.datos += bytes([0x1B, 0x45, 1 if negrita else 0])        # ESC E
        self.datos += bytes([0x1D, 0x21, self.TAMANO[tamano]])        # GS !

    def _ancho(self, tamano):
        return self.ancho // 2 if tamano == 'grande' else self.ancho

    def texto(self, texto='', alinear='izq', negrita=False, tamano='normal', sangria=0):
        self._formato(alinear, negrita, tamano)
        ancho = self._ancho(tamano)
        lineas = textwrap.wrap(str(texto), ancho - sangria) or ['']
        for linea in lineas:
            self.datos += self._codificar(' ' * sangria + linea) + b'\n'
        self._formato('izq', False, 'normal')
        return self

    def fila(self, izquierda, derecha, negrita=False, tamano='normal'):
        """Texto a la izquierda e importe pegado a la derecha."""
        self._formato('izq', negrita, tamano)
        ancho = self._ancho(tamano)
        derecha = str(derecha)
        hueco = max(ancho - len(derecha) - 1, 8)
        lineas = textwrap.wrap(str(izquierda), hueco) or ['']
        self.datos += self._codificar(lineas[0].ljust(ancho - len(derecha)) + derecha) + b'\n'
        for linea in lineas[1:]:
            self.datos += self._codificar(linea) + b'\n'
        self._formato('izq', False, 'normal')
        return self

    def separador(self, caracter='-'):
        self.datos += self._codificar(caracter * self.ancho) + b'\n'
        return self

    def salto(self, lineas=1):
        self.datos += b'\n' * lineas
        return self

    def finalizar(self):
        if self.cortar:
            self.datos += b'\x1d\x56\x42\x00'   # GS V 66 0: avanzar hasta la cuchilla y corte parcial
        else:
            self.datos += b'\x1b\x64\x05'       # ESC d 5: avanzar papel para cortarlo a mano
        return bytes(self.datos)


def comando_cajon(config):
    # ESC p m t1 t2 -> m=0 pin 2, m=1 pin 5; pulso de 50ms on / 500ms off
    pin = 1 if str(config.get('pin_cajon', 2)).strip() == '5' else 0
    return bytes([0x1B, 0x70, pin, 0x19, 0xFA])


def _precio_unitario(item):
    precio = _a_float(item.get('precioUnit'), None)
    if precio is None:
        precio = _a_float(item.get('precioBase')) + PRECIO_EXTRA * len(item.get('extras') or [])
    return precio


def generar_ticket_venta(venta, config):
    """Ticket de cliente con el mismo contenido que el que se ve en pantalla."""
    doc = DocumentoESCPOS(config)
    total = _a_float(venta.get('total'))
    efectivo = _a_float(venta.get('pagado_efectivo'))
    tarjeta = _a_float(venta.get('pagado_tarjeta'))
    cambio = _a_float(venta.get('cambio'))
    metodo = venta.get('metodo_pago') or 'efectivo'

    doc.texto(TICKET_NOMBRE, 'centro', negrita=True)
    for linea in TICKET_CABECERA:
        doc.texto(linea, 'centro')
    doc.separador()
    if metodo == 'anulacion':
        doc.texto('ANULACION DE VENTA', 'centro', negrita=True, tamano='alto')
        doc.texto(f"Venta original: {venta.get('anula_a', '')}", 'centro')
    doc.texto(venta.get('timestamp', ''), 'centro')
    doc.separador()

    for it in venta.get('items') or []:
        qty = _a_int(it.get('qty'), 1)
        doc.fila(f"{qty}x {it.get('nombre', '')}", formato_euros(_precio_unitario(it) * qty))
        for r in it.get('removals') or []:
            doc.texto(r, sangria=3)
        for e in it.get('extras') or []:
            doc.texto(f"{e} +0,80 €", sangria=3)
        if it.get('notes'):
            doc.texto(f"Nota: {it['notes']}", sangria=3)

    doc.separador()
    doc.fila('TOTAL (IVA incl.)', formato_euros(total), negrita=True, tamano='alto')
    base = total / (1 + IVA)
    doc.fila('Base Imponible (10%):', formato_euros(base))
    doc.fila('Cuota IVA:', formato_euros(total - base))
    doc.separador()

    if metodo == 'anulacion':
        if abs(efectivo - cambio) >= 0.005:
            doc.fila('Devuelto en efectivo', formato_euros(-(efectivo - cambio)), negrita=True)
        if abs(tarjeta) >= 0.005:
            doc.fila('Devuelto a tarjeta', formato_euros(-tarjeta), negrita=True)
    elif metodo == 'efectivo':
        doc.fila('Efectivo', formato_euros(efectivo))
        doc.fila('Cambio', formato_euros(cambio))
    elif metodo == 'tarjeta':
        doc.fila('Tarjeta', formato_euros(tarjeta))
    else:
        doc.fila('Efectivo', formato_euros(efectivo))
        doc.fila('Tarjeta', formato_euros(tarjeta))
    doc.separador()
    doc.texto('¡Gracias por su visita!', 'centro', negrita=True)
    return doc.finalizar()


def generar_comanda(comanda, config):
    """Comanda para cocina: letra grande, agrupada por categoria y sin precios."""
    doc = DocumentoESCPOS(config)
    doc.texto('COMANDA COCINA', 'centro', negrita=True, tamano='grande')
    doc.texto(datetime.now().strftime('%d/%m/%Y %H:%M'), 'centro')
    doc.separador('=')
    if comanda.get('pedido_id'):
        doc.texto(f"PEDIDO #{comanda['pedido_id']}", 'centro', negrita=True, tamano='alto')
    hora = str(comanda.get('hora_recogida') or '').strip()
    if hora:
        doc.texto(f"RECOGER: {hora}", 'centro', negrita=True, tamano='grande')

    ultima_categoria = None
    for it in comanda.get('items') or []:
        categoria = str(it.get('categoria') or 'VARIOS').upper()
        if categoria != ultima_categoria:
            doc.salto()
            doc.texto(f"---- {categoria} ----", 'centro', negrita=True)
            ultima_categoria = categoria
        doc.texto(f"{_a_int(it.get('qty'), 1)}x {it.get('nombre', '')}", negrita=True, tamano='alto')
        for r in it.get('removals') or []:
            doc.texto(f"- {r}", negrita=True, sangria=4)
        for e in it.get('extras') or []:
            doc.texto(f"+ {e}", negrita=True, sangria=4)
        if it.get('notes'):
            doc.texto(f"Nota: {it['notes']}", negrita=True, sangria=4)
        doc.separador()
    return doc.finalizar()


def generar_ticket_cierre(cierre, movimientos, config):
    """Justificante en papel del arqueo y cierre de caja."""
    doc = DocumentoESCPOS(config)
    doc.texto('CIERRE DE CAJA', 'centro', negrita=True, tamano='grande')
    doc.texto(TICKET_NOMBRE, 'centro')
    doc.texto(cierre.get('timestamp', ''), 'centro')
    doc.separador()
    doc.fila('Fondo inicial', formato_euros(cierre.get('fondo_inicial')))
    doc.fila('+ Ventas en efectivo', formato_euros(cierre.get('ventas_efectivo')))
    doc.fila('+ Entradas de caja', formato_euros(cierre.get('entradas')))
    doc.fila('- Salidas de caja', formato_euros(cierre.get('salidas')))
    doc.separador()
    doc.fila('Teorico en caja', formato_euros(cierre.get('teorico_efectivo')), negrita=True)
    doc.fila('Contado (real)', formato_euros(cierre.get('real_efectivo')), negrita=True)
    doc.fila('DESCUADRE', formato_euros(cierre.get('descuadre')), negrita=True, tamano='alto')
    doc.separador()
    doc.fila('Pagos con tarjeta', formato_euros(cierre.get('ventas_tarjeta')))
    doc.fila('TOTAL FACTURADO', formato_euros(cierre.get('total_facturado')), negrita=True)

    desglose = cierre.get('desglose') or {}
    lineas_desglose = [(etiqueta, _a_int(desglose.get(clave)), valor)
                       for clave, etiqueta, valor in DESGLOSE_CAJA if _a_int(desglose.get(clave)) > 0]
    if lineas_desglose:
        doc.separador()
        doc.texto('DESGLOSE DEL EFECTIVO', negrita=True)
        for etiqueta, cantidad, valor in lineas_desglose:
            doc.fila(f"{cantidad} x {etiqueta}", formato_euros(cantidad * valor))

    if movimientos:
        doc.separador()
        doc.texto('ENTRADAS Y SALIDAS DE CAJA', negrita=True)
        for m in movimientos:
            hora = (m.get('timestamp') or '')[11:16]
            signo = -1 if m.get('tipo') == 'salida' else 1
            doc.fila(f"{hora} {m.get('concepto', '')}", formato_euros(signo * _a_float(m.get('importe'))))

    doc.separador()
    doc.salto(2)
    doc.texto('Firma: ______________________')
    doc.salto(1)
    return doc.finalizar()


def enviar_a_impresora(datos, nombre_trabajo, config=None):
    """Envia bytes RAW a la impresora configurada.
    Devuelve {'ok', 'configurada', 'error'}; nunca lanza excepciones."""
    if config is None:
        config = leer_config_impresora()
    if win32print is None:
        return {'ok': False, 'configurada': False, 'error': 'Falta instalar pywin32 en el servidor'}
    nombre = str(config.get('impresora', '')).strip()
    if not nombre:
        return {'ok': False, 'configurada': False,
                'error': 'Impresora no configurada en config_impresora.json'}

    handle = None
    try:
        handle = win32print.OpenPrinter(nombre)
        win32print.StartDocPrinter(handle, 1, (nombre_trabajo, None, 'RAW'))
        try:
            win32print.StartPagePrinter(handle)
            win32print.WritePrinter(handle, datos)
            win32print.EndPagePrinter(handle)
        finally:
            win32print.EndDocPrinter(handle)
        return {'ok': True, 'configurada': True, 'error': ''}
    except Exception as e:
        logger.error(f"ERROR imprimiendo '{nombre_trabajo}' en '{nombre}': {e}")
        return {'ok': False, 'configurada': True, 'error': f'No se pudo imprimir en "{nombre}"'}
    finally:
        if handle:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass


def imprimir_venta(venta):
    """Tras guardar una venta: abre el cajon si se ha movido efectivo
    e imprime el ticket si esta activada la impresion automatica."""
    try:
        config = leer_config_impresora()
        datos = b''
        if abs(_a_float(venta.get('pagado_efectivo'))) >= 0.005:
            datos += comando_cajon(config)
        if config.get('imprimir_ticket_automatico'):
            datos += generar_ticket_venta(venta, config)
        if not datos:
            return {'ok': True, 'configurada': True, 'error': ''}
        return enviar_a_impresora(datos, 'Ticket', config)
    except Exception as e:
        logger.error(f"ERROR preparando ticket: {e}")
        logger.error(traceback.format_exc())
        return {'ok': False, 'configurada': True, 'error': 'Error preparando el ticket'}


def imprimir_cierre(cierre):
    try:
        config = leer_config_impresora()
        datos = generar_ticket_cierre(cierre, leer_movimientos_hoy(), config)
        return enviar_a_impresora(datos, 'Cierre de caja', config)
    except Exception as e:
        logger.error(f"ERROR preparando ticket de cierre: {e}")
        logger.error(traceback.format_exc())
        return {'ok': False, 'configurada': True, 'error': 'Error preparando el ticket de cierre'}


def estado_impresora():
    """Estado de la impresora segun la cola de impresion de Windows."""
    config = leer_config_impresora()
    nombre = str(config.get('impresora', '')).strip()
    if win32print is None:
        return {'estado': 'error', 'mensaje': 'Falta instalar pywin32 en el servidor'}
    if not nombre:
        return {'estado': 'sin_configurar', 'mensaje': 'Impresora sin configurar (config_impresora.json)'}

    handle = None
    try:
        handle = win32print.OpenPrinter(nombre)
        info = win32print.GetPrinter(handle, 2)
    except Exception:
        return {'estado': 'error', 'mensaje': f'No se encuentra la impresora "{nombre}" en Windows'}
    finally:
        if handle:
            try:
                win32print.ClosePrinter(handle)
            except Exception:
                pass

    if (info.get('Attributes') or 0) & PRINTER_ATTRIBUTE_WORK_OFFLINE:
        return {'estado': 'error', 'mensaje': f'"{nombre}": en modo "usar sin conexion"'}
    problemas = [texto for bit, texto in ESTADOS_IMPRESORA_ERROR if (info.get('Status') or 0) & bit]
    if problemas:
        return {'estado': 'error', 'mensaje': f'"{nombre}": ' + ', '.join(problemas)}
    trabajos = info.get('cJobs') or 0
    if trabajos >= 2:
        return {'estado': 'aviso',
                'mensaje': f'"{nombre}": {trabajos} trabajos esperando en la cola (¿apagada o desconectada?)'}
    return {'estado': 'ok', 'mensaje': f'"{nombre}" lista'}


def _respuesta_impresion(resultado):
    return jsonify(resultado), (200 if resultado.get('ok') else 503)


@app.route('/api/abrir-cajon', methods=['POST'])
def api_abrir_cajon():
    """Abre el cajon portamonedas."""
    try:
        config = leer_config_impresora()
        return _respuesta_impresion(enviar_a_impresora(comando_cajon(config), 'Abrir cajon', config))
    except Exception as e:
        logger.error(f"ERROR en POST /api/abrir-cajon: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/imprimir/ticket', methods=['POST'])
def api_imprimir_ticket():
    """Imprime (o reimprime) el ticket de una venta."""
    try:
        data = request.json
        if not data:
            return jsonify({'error': 'No se recibieron datos'}), 400
        config = leer_config_impresora()
        return _respuesta_impresion(enviar_a_impresora(generar_ticket_venta(data, config), 'Ticket', config))
    except Exception as e:
        logger.error(f"ERROR en POST /api/imprimir/ticket: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/imprimir/comanda', methods=['POST'])
def api_imprimir_comanda():
    """Imprime la comanda de cocina del pedido actual."""
    try:
        data = request.json
        if not data or not data.get('items'):
            return jsonify({'error': 'La comanda no tiene productos'}), 400
        config = leer_config_impresora()
        return _respuesta_impresion(enviar_a_impresora(generar_comanda(data, config), 'Comanda', config))
    except Exception as e:
        logger.error(f"ERROR en POST /api/imprimir/comanda: {e}")
        logger.error(traceback.format_exc())
        return jsonify({'error': 'Error interno del servidor'}), 500


@app.route('/api/impresora/estado', methods=['GET'])
def api_estado_impresora():
    try:
        return jsonify(estado_impresora())
    except Exception as e:
        logger.error(f"ERROR en GET /api/impresora/estado: {e}")
        return jsonify({'estado': 'error', 'mensaje': 'Error consultando la impresora'}), 500


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

    # Los CSV de versiones anteriores reciben las columnas nuevas (anulaciones, entradas/salidas)
    asegurar_columnas_csv(CSV_VENTAS, VENTAS_FIELDNAMES)
    asegurar_columnas_csv(CSV_CIERRES, CIERRES_FIELDNAMES)

    config = leer_config_impresora()
    if win32print is None:
        print('  [AVISO] pywin32 no instalado - el cajon no se abrira')
        print('          Ejecuta: pip install pywin32')
    elif not str(config.get('impresora', '')).strip():
        print(f'  [AVISO] Impresora del cajon sin configurar en {CONFIG_IMPRESORA}')
        print('          Impresoras instaladas en este PC:')
        try:
            for p in win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS):
                print(f'            - {p[2]}')
        except Exception:
            pass
    else:
        estado = estado_impresora()
        etiqueta = {'ok': '[OK]', 'aviso': '[AVISO]'}.get(estado['estado'], '[ERROR]')
        print(f"  {etiqueta} Impresora de tickets: {estado['mensaje']}")
    print('')
    
def leer_ventas_rango(desde, hasta):
    """Lee las ventas entre dos fechas (YYYY-MM-DD)."""
    ventas = []
    try:
        if not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0:
            return ventas
        with open(CSV_VENTAS, newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    ts = row.get('timestamp', '')[:10] # Extrae solo la fecha YYYY-MM-DD
                    if desde <= ts <= hasta:
                        try:
                            row['items'] = json.loads(row.get('items_json', '[]'))
                        except:
                            row['items'] = []
                        row.pop('items_json', None)
                        ventas.append(row)
                except Exception as e:
                    continue
    except Exception as e:
        logger.error(f"ERROR leyendo ventas rango: {e}")
    return ventas

@app.route('/api/estadisticas', methods=['GET'])
def api_get_estadisticas():
    desde = request.args.get('desde', '2000-01-01')
    hasta = request.args.get('hasta', '2099-12-31')
    try:
        return jsonify(leer_ventas_rango(desde, hasta))
    except Exception as e:
        logger.error(f"ERROR en GET /api/estadisticas: {e}")
        return jsonify({'error': 'Error interno'}), 500


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
