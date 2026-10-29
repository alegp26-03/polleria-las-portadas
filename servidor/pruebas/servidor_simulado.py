"""
Arranca el TPV real con una impresora simulada y datos en una carpeta temporal.
Lo usa test_navegador.mjs; no toca ventas.csv ni el resto de datos reales.

    python servidor_simulado.py 5099 [carpeta_datos]
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from impresora_simulada import ImpresoraSimulada, texto_impreso, CAJON_PIN2, CAJON_PIN5  # noqa: E402

SERVIDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# La carpeta de datos la puede indicar quien lanza las pruebas (para borrarla al terminar)
directorio = sys.argv[2] if len(sys.argv) > 2 else tempfile.mkdtemp(prefix='tpv_navegador_')
for archivo in ('tpv.py', 'index.html', 'productos.csv'):
    shutil.copy(os.path.join(SERVIDOR_DIR, archivo), directorio)
spec = importlib.util.spec_from_file_location('tpv_simulado', os.path.join(directorio, 'tpv.py'))
tpv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tpv)

impresora = ImpresoraSimulada()
tpv.win32print = impresora
CSV_VENTAS_REAL = tpv.CSV_VENTAS


def configurar(**opciones):
    config = dict(tpv.leer_config_impresora(), **opciones)
    with open(tpv.CONFIG_IMPRESORA, 'w', encoding='utf-8') as f:
        json.dump(config, f)


configurar(impresora='TM-T20')
app = tpv.app


@app.route('/__pruebas/trabajos')
def pruebas_trabajos():
    return tpv.jsonify([{
        'nombre': t['nombre'],
        'abre_cajon': t['datos'].startswith((CAJON_PIN2, CAJON_PIN5)),
        'solo_cajon': t['datos'] in (CAJON_PIN2, CAJON_PIN5),
        'texto': '\n'.join(texto_impreso(t['datos']))
    } for t in impresora.trabajos])


@app.route('/__pruebas/config', methods=['POST'])
def pruebas_config():
    configurar(**tpv.request.json)
    return tpv.jsonify({'ok': True})


@app.route('/__pruebas/impresora', methods=['POST'])
def pruebas_impresora():
    impresora.impresoras['TM-T20'].update(tpv.request.json)
    return tpv.jsonify({'ok': True})


@app.route('/__pruebas/ventas_mismo_segundo', methods=['POST'])
def pruebas_ventas_mismo_segundo():
    """Registra dos ventas en efectivo con el mismo timestamp."""
    ts = tpv.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    for nombre in ('Venta gemela A', 'Venta gemela B'):
        tpv.registrar_venta({'timestamp': ts, 'total': '3.00', 'pagado_efectivo': '3.00', 'pagado_tarjeta': '0.00',
                             'cambio': '0.00', 'metodo_pago': 'efectivo',
                             'items': [{'nombre': nombre, 'qty': 1, 'precioUnit': 3.0}]})
    return tpv.jsonify({'timestamp': ts})


@app.route('/__pruebas/ventas_rotas', methods=['POST'])
def pruebas_ventas_rotas():
    """Simula que ventas.csv no se puede escribir (archivo bloqueado, disco lleno...)."""
    if tpv.request.json.get('rotas'):
        tpv.CSV_VENTAS = os.path.join(directorio, 'ventas_bloqueadas')
        os.makedirs(tpv.CSV_VENTAS, exist_ok=True)
    else:
        tpv.CSV_VENTAS = CSV_VENTAS_REAL
    return tpv.jsonify({'ok': True})


if __name__ == '__main__':
    tpv.arranque_seguro()
    app.run(host='127.0.0.1', port=int(sys.argv[1]) if len(sys.argv) > 1 else 5099, threaded=True)
