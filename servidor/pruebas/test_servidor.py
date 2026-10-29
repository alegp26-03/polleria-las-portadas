"""
Pruebas del servidor del TPV (impresora, cajon, ventas, anulaciones, caja).

No necesitan impresora ni pywin32: se usa una impresora simulada que guarda
los bytes exactos que recibiria la impresora real.

Ejecutar desde la carpeta servidor:
    python -m unittest discover -s pruebas -v
"""
import csv
import importlib.util
import json
import logging
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime

SERVIDOR_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from impresora_simulada import ImpresoraSimulada, texto_impreso, CAJON_PIN2, CAJON_PIN5, CORTE  # noqa: E402


def cargar_tpv():
    """Carga una copia de tpv.py en una carpeta temporal para no tocar los datos reales."""
    directorio = tempfile.mkdtemp(prefix='tpv_pruebas_')
    shutil.copy(os.path.join(SERVIDOR_DIR, 'tpv.py'), directorio)
    spec = importlib.util.spec_from_file_location('tpv_bajo_prueba', os.path.join(directorio, 'tpv.py'))
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo, directorio


tpv, DIRECTORIO_MODULO = cargar_tpv()


def tearDownModule():
    for logger in (logging.getLogger('TPV'), logging.getLogger()):
        for h in list(logger.handlers):
            h.close()
            logger.removeHandler(h)
    shutil.rmtree(DIRECTORIO_MODULO, ignore_errors=True)


class PruebaTPV(unittest.TestCase):
    """Cada prueba trabaja en una carpeta vacia con su propia impresora simulada."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix='tpv_datos_')
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        rutas = {
            'CSV_PROD': 'productos.csv', 'CSV_VENTAS': 'ventas.csv',
            'CSV_PEDIDOS': 'pedidos_pendientes.csv', 'CSV_CIERRES': 'cierres.csv',
            'CSV_MOVIMIENTOS': 'movimientos_caja.csv', 'CONFIG_IMPRESORA': 'config_impresora.json',
            'BACKUP_DIR': 'backups'
        }
        for atributo, archivo in rutas.items():
            original = getattr(tpv, atributo)
            setattr(tpv, atributo, os.path.join(self.dir, archivo))
            self.addCleanup(setattr, tpv, atributo, original)

        self.impresora = ImpresoraSimulada()
        original = tpv.win32print
        tpv.win32print = self.impresora
        self.addCleanup(setattr, tpv, 'win32print', original)

        self.configurar(impresora='TM-T20')
        self.cliente = tpv.app.test_client()

    # --- utilidades ---------------------------------------------------------

    def configurar(self, **opciones):
        config = dict(tpv.CONFIG_IMPRESORA_DEFECTO, **opciones)
        with open(tpv.CONFIG_IMPRESORA, 'w', encoding='utf-8') as f:
            json.dump(config, f)

    def leer_csv(self, ruta):
        with open(ruta, newline='', encoding='utf-8') as f:
            return list(csv.DictReader(f))

    def venta(self, **cambios):
        datos = {
            'metodo_pago': 'efectivo', 'pagado_efectivo': '20.00', 'pagado_tarjeta': '0.00',
            'cambio': '4.50', 'total': '15.50',
            'items': [
                {'nombre': 'Pollo asado', 'qty': 1, 'precioBase': 12.0, 'precioUnit': 12.0,
                 'removals': [], 'extras': [], 'notes': ''},
                {'nombre': 'Patatas fritas', 'qty': 2, 'precioBase': 0.95, 'precioUnit': 1.75,
                 'removals': ['Sin sal'], 'extras': ['Extra salsa'], 'notes': 'Bien hechas 🍟'}
            ]
        }
        datos.update(cambios)
        return datos

    def cobrar(self, **cambios):
        return self.cliente.post('/api/venta', json=self.venta(**cambios))


class TestDocumentoESCPOS(PruebaTPV):

    def test_caracteres_espanoles_y_euro_en_tabla_858(self):
        doc = tpv.DocumentoESCPOS(tpv.leer_config_impresora())
        doc.texto('Pollería ñ ¡Hola! 5,00 €')
        datos = doc.finalizar()
        self.assertTrue(datos.startswith(b'\x1b\x40\x1b\x74\x13'))  # ESC @ + ESC t 19
        self.assertIn('Pollería ñ ¡Hola! 5,00 €'.encode('cp858'), datos)
        self.assertIn(b'\xd5', datos)  # el euro en PC858

    def test_emojis_se_omiten_sin_romper_el_ticket(self):
        doc = tpv.DocumentoESCPOS(tpv.leer_config_impresora())
        doc.texto('Nota 📝 importante')
        self.assertIn('Nota  importante', texto_impreso(doc.finalizar()))

    def test_ninguna_linea_supera_el_ancho_del_papel(self):
        datos = tpv.generar_ticket_venta(self.venta(items=[{
            'nombre': 'Hamburguesa especial de la casa con doble de todo y salsa secreta',
            'qty': 3, 'precioUnit': 9.5, 'removals': [], 'extras': [], 'notes': 'x' * 120}]),
            tpv.leer_config_impresora())
        for linea in texto_impreso(datos):
            self.assertLessEqual(len(linea), 48, linea)

    def test_importes_alineados_a_la_derecha(self):
        lineas = texto_impreso(tpv.generar_ticket_venta(self.venta(), tpv.leer_config_impresora()))
        fila = next(l for l in lineas if l.startswith('1x Pollo asado'))
        self.assertEqual(len(fila), 48)
        self.assertTrue(fila.endswith('12.00 €'))

    def test_ancho_configurable_para_papel_de_58mm(self):
        self.configurar(impresora='TM-T20', ancho_caracteres=32)
        lineas = texto_impreso(tpv.generar_ticket_venta(self.venta(), tpv.leer_config_impresora()))
        self.assertIn('-' * 32, lineas)
        self.assertTrue(all(len(l) <= 32 for l in lineas))

    def test_corte_de_papel_activado_y_desactivado(self):
        self.assertTrue(tpv.generar_comanda({'items': [{'nombre': 'A', 'qty': 1}]},
                                            tpv.leer_config_impresora()).endswith(CORTE))
        self.configurar(impresora='TM-T20', cortar_papel=False)
        datos = tpv.generar_comanda({'items': [{'nombre': 'A', 'qty': 1}]}, tpv.leer_config_impresora())
        self.assertNotIn(CORTE, datos)
        self.assertTrue(datos.endswith(b'\x1b\x64\x05'))

    def test_codificacion_desconocida_usa_la_de_por_defecto(self):
        doc = tpv.DocumentoESCPOS(dict(tpv.CONFIG_IMPRESORA_DEFECTO, codificacion='no-existe'))
        self.assertEqual(doc.codificacion, 'cp858')

    def test_comando_cajon_pin_2_y_pin_5(self):
        self.assertEqual(tpv.comando_cajon({'pin_cajon': 2}), CAJON_PIN2)
        self.assertEqual(tpv.comando_cajon({'pin_cajon': '5'}), CAJON_PIN5)
        self.assertEqual(tpv.comando_cajon({}), CAJON_PIN2)


class TestConfiguracion(PruebaTPV):

    def test_crea_el_archivo_si_no_existe(self):
        os.remove(tpv.CONFIG_IMPRESORA)
        config = tpv.leer_config_impresora()
        self.assertEqual(config['impresora'], '')
        with open(tpv.CONFIG_IMPRESORA, encoding='utf-8') as f:
            self.assertEqual(json.load(f), tpv.CONFIG_IMPRESORA_DEFECTO)

    def test_completa_opciones_nuevas_sin_perder_las_del_usuario(self):
        with open(tpv.CONFIG_IMPRESORA, 'w', encoding='utf-8') as f:
            json.dump({'impresora': 'Mi impresora', 'pin_cajon': 5}, f)
        tpv.leer_config_impresora()
        with open(tpv.CONFIG_IMPRESORA, encoding='utf-8') as f:
            guardado = json.load(f)
        self.assertEqual(guardado['impresora'], 'Mi impresora')
        self.assertEqual(guardado['pin_cajon'], 5)
        self.assertTrue(guardado['cortar_papel'])

    def test_lee_archivo_guardado_con_bom_del_bloc_de_notas(self):
        with open(tpv.CONFIG_IMPRESORA, 'w', encoding='utf-8-sig') as f:
            json.dump(dict(tpv.CONFIG_IMPRESORA_DEFECTO, impresora='TM-T20'), f)
        self.assertEqual(tpv.leer_config_impresora()['impresora'], 'TM-T20')

    def test_json_roto_no_tumba_el_servidor_ni_borra_el_archivo(self):
        with open(tpv.CONFIG_IMPRESORA, 'w', encoding='utf-8') as f:
            f.write('{"impresora": "TM-T20",')
        self.assertEqual(tpv.leer_config_impresora()['impresora'], '')
        with open(tpv.CONFIG_IMPRESORA, encoding='utf-8') as f:
            self.assertEqual(f.read(), '{"impresora": "TM-T20",')
        respuesta = self.cobrar()
        self.assertEqual(respuesta.status_code, 201)
        self.assertFalse(respuesta.get_json()['impresora']['ok'])


class TestCobro(PruebaTPV):

    def test_efectivo_guarda_venta_abre_cajon_e_imprime_ticket(self):
        respuesta = self.cobrar()
        self.assertEqual(respuesta.status_code, 201)
        rd = respuesta.get_json()
        self.assertTrue(rd['ok'])
        self.assertEqual(rd['impresora'], {'ok': True, 'configurada': True, 'error': ''})

        ventas = self.leer_csv(tpv.CSV_VENTAS)
        self.assertEqual(len(ventas), 1)
        self.assertEqual(ventas[0]['total'], '15.50')
        self.assertEqual(ventas[0]['anula_a'], '')

        self.assertEqual(len(self.impresora.trabajos), 1)
        trabajo = self.impresora.trabajos[0]
        self.assertEqual(trabajo['tipo'], 'RAW')
        self.assertTrue(trabajo['datos'].startswith(CAJON_PIN2), 'el cajon se abre antes de imprimir')
        self.assertTrue(trabajo['datos'].endswith(CORTE))
        texto = '\n'.join(texto_impreso(trabajo['datos']))
        for esperado in ['POLLERIA FREIDURIA LAS PORTADAS', 'DNI: 48964063F', '1x Pollo asado',
                         '2x Patatas fritas', 'Sin sal', 'Extra salsa +0,80 €', 'Nota: Bien hechas',
                         'TOTAL (IVA incl.)', '15.50 €', 'Base Imponible (10%):', '14.09 €',
                         'Cuota IVA:', '1.41 €', 'Efectivo', '20.00 €', 'Cambio', '4.50 €',
                         '¡Gracias por su visita!', rd['timestamp']]:
            self.assertIn(esperado, texto)

    def test_tarjeta_imprime_ticket_sin_abrir_cajon(self):
        self.cobrar(metodo_pago='tarjeta', pagado_efectivo='0.00', pagado_tarjeta='15.50', cambio='0.00')
        datos = self.impresora.trabajos[0]['datos']
        self.assertNotIn(CAJON_PIN2, datos)
        self.assertIn('Tarjeta', '\n'.join(texto_impreso(datos)))

    def test_mixto_abre_cajon_solo_si_hay_parte_en_efectivo(self):
        self.cobrar(metodo_pago='mixto', pagado_efectivo='5.00', pagado_tarjeta='10.50', cambio='0.00')
        self.cobrar(metodo_pago='mixto', pagado_efectivo='0.00', pagado_tarjeta='15.50', cambio='0.00')
        self.assertIn(CAJON_PIN2, self.impresora.trabajos[0]['datos'])
        self.assertNotIn(CAJON_PIN2, self.impresora.trabajos[1]['datos'])

    def test_sin_ticket_automatico_solo_abre_el_cajon(self):
        self.configurar(impresora='TM-T20', imprimir_ticket_automatico=False)
        self.cobrar()
        self.cobrar(metodo_pago='tarjeta', pagado_efectivo='0.00', pagado_tarjeta='15.50', cambio='0.00')
        self.assertEqual([t['datos'] for t in self.impresora.trabajos], [CAJON_PIN2])

    def test_cajon_en_pin_5(self):
        self.configurar(impresora='TM-T20', pin_cajon=5)
        self.cobrar()
        self.assertTrue(self.impresora.trabajos[0]['datos'].startswith(CAJON_PIN5))

    def test_sin_impresora_configurada_la_venta_se_guarda_igual(self):
        self.configurar(impresora='')
        rd = self.cobrar().get_json()
        self.assertTrue(rd['ok'])
        self.assertEqual(rd['impresora']['configurada'], False)
        self.assertEqual(len(self.leer_csv(tpv.CSV_VENTAS)), 1)

    def test_sin_pywin32_la_venta_se_guarda_igual(self):
        tpv.win32print = None
        rd = self.cobrar().get_json()
        self.assertTrue(rd['ok'])
        self.assertIn('pywin32', rd['impresora']['error'])
        self.assertEqual(len(self.leer_csv(tpv.CSV_VENTAS)), 1)

    def test_fallo_de_impresora_no_pierde_la_venta(self):
        self.impresora.fallar = True
        rd = self.cobrar().get_json()
        self.assertTrue(rd['ok'])
        self.assertFalse(rd['impresora']['ok'])
        self.assertTrue(rd['impresora']['configurada'])
        self.assertEqual(len(self.leer_csv(tpv.CSV_VENTAS)), 1)

    def test_impresora_con_nombre_incorrecto(self):
        self.configurar(impresora='No existe')
        rd = self.cobrar().get_json()
        self.assertTrue(rd['ok'])
        self.assertIn('No existe', rd['impresora']['error'])

    def test_venta_sin_productos_o_total_invalido_se_rechaza(self):
        self.assertEqual(self.cobrar(items=[]).status_code, 400)
        self.assertEqual(self.cobrar(total='abc').status_code, 400)
        self.assertFalse(os.path.exists(tpv.CSV_VENTAS))
        self.assertEqual(self.impresora.trabajos, [])

    def test_si_no_se_puede_guardar_no_abre_cajon_y_devuelve_error(self):
        os.makedirs(tpv.CSV_VENTAS)  # una carpeta en lugar del CSV: la escritura falla
        respuesta = self.cobrar()
        self.assertEqual(respuesta.status_code, 500)
        self.assertNotIn('ok', respuesta.get_json())
        self.assertEqual(self.impresora.trabajos, [])

    def test_cobro_de_pedido_telefonico(self):
        rd = self.cliente.post('/api/pedido', json={
            'telefono': '600111222', 'nombre_cliente': 'Ana', 'total': '15.50',
            'items': self.venta()['items']}).get_json()
        pid = rd['pedido']['id']
        respuesta = self.cliente.post(f'/api/pedido/{pid}/cobrar', json=self.venta())
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.get_json()['impresora']['ok'])
        self.assertTrue(self.impresora.trabajos[0]['datos'].startswith(CAJON_PIN2))
        self.assertEqual(self.leer_csv(tpv.CSV_PEDIDOS)[0]['estado'], 'recogido')
        # Un segundo cobro del mismo pedido no se permite
        self.assertEqual(self.cliente.post(f'/api/pedido/{pid}/cobrar', json=self.venta()).status_code, 404)
        self.assertEqual(len(self.impresora.trabajos), 1)


class TestAperturaManualYReimpresion(PruebaTPV):

    def test_boton_abrir_cajon(self):
        respuesta = self.cliente.post('/api/abrir-cajon')
        self.assertEqual(respuesta.status_code, 200)
        self.assertEqual(self.impresora.trabajos[0]['datos'], CAJON_PIN2)

    def test_boton_abrir_cajon_sin_configurar(self):
        self.configurar(impresora='')
        respuesta = self.cliente.post('/api/abrir-cajon')
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.get_json()['configurada'], False)

    def test_reimprimir_ticket_no_abre_cajon(self):
        respuesta = self.cliente.post('/api/imprimir/ticket', json=dict(self.venta(), timestamp='2026-10-29 13:00:00'))
        self.assertEqual(respuesta.status_code, 200)
        datos = self.impresora.trabajos[0]['datos']
        self.assertNotIn(CAJON_PIN2, datos)
        self.assertIn('2026-10-29 13:00:00', '\n'.join(texto_impreso(datos)))

    def test_comanda_de_cocina(self):
        respuesta = self.cliente.post('/api/imprimir/comanda', json={
            'pedido_id': 7, 'hora_recogida': '14:30',
            'items': [
                {'qty': 2, 'nombre': 'Pollo asado', 'categoria': 'POLLOS', 'removals': [], 'extras': [], 'notes': ''},
                {'qty': 1, 'nombre': 'Bocata lomo', 'categoria': 'BOCATAS', 'removals': ['Sin tomate'],
                 'extras': ['Extra queso'], 'notes': 'Pan tostado'}
            ]})
        self.assertEqual(respuesta.status_code, 200)
        datos = self.impresora.trabajos[0]['datos']
        self.assertNotIn(CAJON_PIN2, datos)
        self.assertTrue(datos.endswith(CORTE))
        texto = '\n'.join(texto_impreso(datos))
        for esperado in ['COMANDA COCINA', 'PEDIDO #7', 'RECOGER: 14:30', '---- POLLOS ----', '2x Pollo asado',
                         '---- BOCATAS ----', '- Sin tomate', '+ Extra queso', 'Nota: Pan tostado']:
            self.assertIn(esperado, texto)
        self.assertNotIn('€', texto)

    def test_comanda_vacia_o_sin_impresora(self):
        self.assertEqual(self.cliente.post('/api/imprimir/comanda', json={'items': []}).status_code, 400)
        self.configurar(impresora='')
        respuesta = self.cliente.post('/api/imprimir/comanda', json={'items': [{'nombre': 'A', 'qty': 1}]})
        self.assertEqual(respuesta.status_code, 503)
        self.assertEqual(respuesta.get_json()['configurada'], False)


class TestAnulaciones(PruebaTPV):

    def test_anular_venta_en_efectivo(self):
        ts = self.cobrar().get_json()['timestamp']
        respuesta = self.cliente.post('/api/venta/anular', json={'timestamp': ts})
        self.assertEqual(respuesta.status_code, 201)

        ventas = self.cliente.get('/api/ventas').get_json()
        self.assertEqual(len(ventas), 2)
        anulacion = ventas[1]
        self.assertEqual(anulacion['metodo_pago'], 'anulacion')
        self.assertEqual(anulacion['anula_a'], ts)
        self.assertEqual(anulacion['total'], '-15.50')
        self.assertEqual([it['qty'] for it in anulacion['items']], [-1, -2])

        # El dia queda a cero, igual que lo calcula el cierre de caja
        self.assertAlmostEqual(sum(float(v['total']) for v in ventas), 0)
        self.assertAlmostEqual(sum(float(v['pagado_efectivo']) - float(v['cambio']) for v in ventas), 0)

        datos = self.impresora.trabajos[1]['datos']
        self.assertTrue(datos.startswith(CAJON_PIN2), 'se abre el cajon para devolver el dinero')
        texto = '\n'.join(texto_impreso(datos))
        self.assertIn('ANULACION DE VENTA', texto)
        self.assertIn(f'Venta original: {ts}', texto)
        self.assertIn('Devuelto en efectivo', texto)
        self.assertIn('15.50 €', texto)

    def test_anular_venta_con_tarjeta_no_abre_cajon(self):
        ts = self.cobrar(metodo_pago='tarjeta', pagado_efectivo='0.00',
                         pagado_tarjeta='15.50', cambio='0.00').get_json()['timestamp']
        self.cliente.post('/api/venta/anular', json={'timestamp': ts})
        datos = self.impresora.trabajos[1]['datos']
        self.assertNotIn(CAJON_PIN2, datos)
        self.assertIn('Devuelto a tarjeta', '\n'.join(texto_impreso(datos)))
        with open(tpv.CSV_VENTAS, encoding='utf-8') as f:
            self.assertNotIn('-0.00', f.read())

    def test_no_se_puede_anular_dos_veces(self):
        ts = self.cobrar().get_json()['timestamp']
        self.cliente.post('/api/venta/anular', json={'timestamp': ts})
        respuesta = self.cliente.post('/api/venta/anular', json={'timestamp': ts})
        self.assertEqual(respuesta.status_code, 409)
        self.assertEqual(len(self.leer_csv(tpv.CSV_VENTAS)), 2)

    def test_dos_ventas_en_el_mismo_segundo(self):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for _ in range(2):
            tpv.registrar_venta(dict(self.venta(), timestamp=ts))
        self.assertEqual(self.cliente.post('/api/venta/anular', json={'timestamp': ts}).status_code, 201)
        self.assertEqual(self.cliente.post('/api/venta/anular', json={'timestamp': ts}).status_code, 201)
        self.assertEqual(self.cliente.post('/api/venta/anular', json={'timestamp': ts}).status_code, 409)

    def test_anular_una_venta_concreta_entre_varias_del_mismo_segundo(self):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for nombre in ('A', 'B', 'C'):
            tpv.registrar_venta(dict(self.venta(items=[{'nombre': nombre, 'qty': 1, 'precioUnit': 1}], total='1.00'),
                                     timestamp=ts))
        anular = lambda **extra: self.cliente.post('/api/venta/anular', json=dict(timestamp=ts, **extra))

        respuesta = anular(ocurrencia=1)
        self.assertEqual(respuesta.status_code, 201)
        anulacion = respuesta.get_json()['anulacion']
        self.assertEqual(anulacion['anula_a'], f'{ts}#1')
        self.assertEqual(anulacion['items'][0]['nombre'], 'B', 'se ha anulado una venta distinta a la elegida')
        self.assertNotIn('#', '\n'.join(texto_impreso(self.impresora.trabajos[-1]['datos'])),
                         'el ticket muestra la referencia interna')

        self.assertEqual(anular(ocurrencia=1).status_code, 409)
        self.assertEqual(anular(ocurrencia=7).status_code, 404)
        # Sin indicar cual, se anula la primera libre (A) y luego la C
        self.assertEqual(anular().get_json()['anulacion']['items'][0]['nombre'], 'A')
        self.assertEqual(anular().get_json()['anulacion']['items'][0]['nombre'], 'C')
        self.assertEqual(anular().status_code, 409)

    def test_anulaciones_antiguas_sin_posicion_siguen_contando(self):
        ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for _ in range(2):
            tpv.registrar_venta(dict(self.venta(), timestamp=ts))
        # Anulación guardada por una versión anterior (sin '#n'): corresponde a la primera venta
        tpv.registrar_venta(dict(self.venta(total='-15.50'), metodo_pago='anulacion', anula_a=ts, timestamp=ts))
        anular = lambda **extra: self.cliente.post('/api/venta/anular', json=dict(timestamp=ts, **extra))
        self.assertEqual(anular(ocurrencia=0).status_code, 409)
        self.assertEqual(anular(ocurrencia=1).status_code, 201)
        self.assertEqual(anular().status_code, 409)

    def test_venta_inexistente_de_otro_dia_o_anulacion(self):
        self.assertEqual(self.cliente.post('/api/venta/anular', json={'timestamp': '2020-01-01 10:00:00'}).status_code, 404)
        self.assertEqual(self.cliente.post('/api/venta/anular', json={}).status_code, 400)
        ts = self.cobrar().get_json()['timestamp']
        anulacion = self.cliente.post('/api/venta/anular', json={'timestamp': ts}).get_json()['anulacion']
        respuesta = self.cliente.post('/api/venta/anular', json={'timestamp': anulacion['timestamp']})
        self.assertIn(respuesta.status_code, (404, 409))
        self.assertEqual(len(self.leer_csv(tpv.CSV_VENTAS)), 2)

    def test_estadisticas_restan_lo_anulado(self):
        ts = self.cobrar().get_json()['timestamp']
        self.cobrar()
        self.cliente.post('/api/venta/anular', json={'timestamp': ts})
        hoy = datetime.now().strftime('%Y-%m-%d')
        ventas = self.cliente.get(f'/api/estadisticas?desde={hoy}&hasta={hoy}').get_json()
        pollos = sum(it['qty'] for v in ventas for it in v['items'] if it['nombre'] == 'Pollo asado')
        self.assertEqual(pollos, 1)


class TestMigracionCSV(PruebaTPV):

    def test_ventas_csv_antiguo_recibe_la_columna_nueva_sin_perder_datos(self):
        with open(tpv.CSV_VENTAS, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['timestamp', 'items_json', 'total', 'pagado_efectivo', 'pagado_tarjeta', 'cambio', 'metodo_pago'])
            w.writerow(['2026-01-01 12:00:00', '[{"nombre": "Pollo", "qty": 1}]', '12.00', '12.00', '0.00', '0.00', 'efectivo'])
        self.cobrar()
        filas = self.leer_csv(tpv.CSV_VENTAS)
        self.assertEqual(len(filas), 2)
        self.assertEqual(filas[0]['total'], '12.00')
        self.assertEqual(filas[0]['anula_a'], '')
        self.assertEqual(filas[1]['total'], '15.50')
        self.assertTrue(os.path.exists(os.path.join(self.dir, 'ventas_backup_1.csv')))

    def test_cierres_csv_antiguo(self):
        with open(tpv.CSV_CIERRES, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow(['timestamp', 'fondo_inicial', 'ventas_efectivo', 'ventas_tarjeta', 'teorico_efectivo',
                        'real_efectivo', 'descuadre', 'desglose_json'])
            w.writerow(['2026-05-28 18:22:43', '50.00', '0.00', '0.00', '50.00', '50.00', '0.00', '{}'])
        self.cliente.post('/api/cierre', json={'fondo_inicial': '50.00', 'salidas': '10.00'})
        filas = self.leer_csv(tpv.CSV_CIERRES)
        self.assertEqual(filas[0]['fondo_inicial'], '50.00')
        self.assertEqual(filas[0]['salidas'], '')
        self.assertEqual(filas[1]['salidas'], '10.00')


class TestMovimientosYCierre(PruebaTPV):

    def test_salida_de_caja_guarda_y_abre_cajon(self):
        respuesta = self.cliente.post('/api/movimientos', json={'tipo': 'salida', 'importe': '25', 'concepto': 'Pago proveedor'})
        self.assertEqual(respuesta.status_code, 201)
        self.assertTrue(respuesta.get_json()['impresora']['ok'])
        self.assertEqual(self.impresora.trabajos[0]['datos'], CAJON_PIN2)
        movs = self.cliente.get('/api/movimientos').get_json()
        self.assertEqual(len(movs), 1)
        self.assertEqual((movs[0]['tipo'], movs[0]['importe'], movs[0]['concepto']), ('salida', '25.00', 'Pago proveedor'))

    def test_validacion_de_movimientos(self):
        for datos in [{'tipo': 'otro', 'importe': '5', 'concepto': 'x'},
                      {'tipo': 'entrada', 'importe': '0', 'concepto': 'x'},
                      {'tipo': 'entrada', 'importe': '-3', 'concepto': 'x'},
                      {'tipo': 'entrada', 'importe': 'abc', 'concepto': 'x'},
                      {'tipo': 'entrada', 'importe': '5', 'concepto': '   '}]:
            self.assertEqual(self.cliente.post('/api/movimientos', json=datos).status_code, 400, datos)
        self.assertEqual(self.cliente.get('/api/movimientos').get_json(), [])
        self.assertEqual(self.impresora.trabajos, [])

    def test_movimientos_de_otros_dias_no_cuentan(self):
        tpv.anadir_fila_csv(tpv.CSV_MOVIMIENTOS, {'timestamp': '2020-01-01 10:00:00', 'tipo': 'entrada',
                                                  'importe': '99.00', 'concepto': 'viejo'}, tpv.MOVIMIENTOS_FIELDNAMES)
        self.assertEqual(self.cliente.get('/api/movimientos').get_json(), [])

    def test_cierre_guarda_e_imprime_justificante(self):
        self.cliente.post('/api/movimientos', json={'tipo': 'entrada', 'importe': '30', 'concepto': 'Cambio del banco'})
        self.cliente.post('/api/movimientos', json={'tipo': 'salida', 'importe': '12.40', 'concepto': 'Hielo'})
        respuesta = self.cliente.post('/api/cierre', json={
            'fondo_inicial': '50.00', 'ventas_efectivo': '100.00', 'ventas_tarjeta': '40.00',
            'entradas': '30.00', 'salidas': '12.40', 'teorico_efectivo': '167.60', 'real_efectivo': '167.00',
            'descuadre': '-0.60', 'total_facturado': '140.00',
            'desglose': {'50e': 2, '20e': 3, '5e': 1, '2e': 0, '1e': 2, '50c': 0, '1c': 0}})
        self.assertEqual(respuesta.status_code, 200)
        self.assertTrue(respuesta.get_json()['impresora']['ok'])

        cierre = self.leer_csv(tpv.CSV_CIERRES)[0]
        self.assertEqual((cierre['entradas'], cierre['salidas'], cierre['descuadre']), ('30.00', '12.40', '-0.60'))

        datos = self.impresora.trabajos[-1]['datos']
        self.assertNotIn(CAJON_PIN2, datos)
        self.assertTrue(datos.endswith(CORTE))
        texto = '\n'.join(texto_impreso(datos))
        for esperado in ['CIERRE DE CAJA', 'Fondo inicial', '50.00 €', '+ Ventas en efectivo', '100.00 €',
                         '+ Entradas de caja', '30.00 €', '- Salidas de caja', '12.40 €',
                         'Teorico en caja', '167.60 €', 'Contado (real)', '167.00 €', 'DESCUADRE', '-0.60 €',
                         'TOTAL FACTURADO', '140.00 €', '2 x 50 EUR', '100.00 €', '3 x 20 EUR', '60.00 €',
                         '1 x 5 EUR', '2 x 1 EUR', 'Cambio del banco', 'Hielo', '-12.40 €', 'Firma:']:
            self.assertIn(esperado, texto)
        self.assertNotIn('x 2 EUR', texto, 'no se imprimen monedas con cantidad 0')

    def test_cierre_sin_impresora_se_guarda_igual(self):
        self.configurar(impresora='')
        respuesta = self.cliente.post('/api/cierre', json={'fondo_inicial': '50.00'})
        self.assertEqual(respuesta.status_code, 200)
        self.assertFalse(respuesta.get_json()['impresora']['ok'])
        self.assertEqual(len(self.leer_csv(tpv.CSV_CIERRES)), 1)


class TestEstadoImpresora(PruebaTPV):

    def estado(self):
        return self.cliente.get('/api/impresora/estado').get_json()

    def test_lista(self):
        self.assertEqual(self.estado()['estado'], 'ok')

    def test_sin_configurar_y_sin_pywin32(self):
        self.configurar(impresora='')
        self.assertEqual(self.estado()['estado'], 'sin_configurar')
        tpv.win32print = None
        self.assertEqual(self.estado()['estado'], 'error')

    def test_impresora_inexistente(self):
        self.configurar(impresora='Otra')
        e = self.estado()
        self.assertEqual(e['estado'], 'error')
        self.assertIn('No se encuentra', e['mensaje'])

    def test_sin_papel_desconectada_y_trabajos_atascados(self):
        info = self.impresora.impresoras['TM-T20']
        info['Status'] = 0x10
        self.assertIn('Sin papel', self.estado()['mensaje'])
        info['Status'] = 0
        info['Attributes'] = 0x400
        self.assertIn('sin conexion', self.estado()['mensaje'])
        info['Attributes'] = 0
        info['cJobs'] = 3
        e = self.estado()
        self.assertEqual(e['estado'], 'aviso')
        self.assertIn('3 trabajos', e['mensaje'])


if __name__ == '__main__':
    unittest.main()
