/*
 * Pruebas del TPV en un navegador real (Chrome o Edge sin ventana).
 * Pulsa los botones de la pantalla contra el servidor con impresora simulada
 * y comprueba lo que llega a la impresora.
 *
 * Ejecutar desde la carpeta servidor (necesita Node 22+ y Python con Flask):
 *     node pruebas/test_navegador.mjs [carpeta_para_capturas]
 */
import { spawn } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const AQUI = dirname(fileURLToPath(import.meta.url));
const PUERTO = 5099;
const PUERTO_CDP = 9339;
const BASE = `http://127.0.0.1:${PUERTO}`;
const CAPTURAS = process.argv[2];
const NAVEGADOR = [
  process.env.CHROME,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
].find(p => p && existsSync(p));

const sleep = ms => new Promise(r => setTimeout(r, ms));
async function esperar(fn, desc, ms = 6000) {
  const inicio = Date.now();
  let ultimo;
  while (Date.now() - inicio < ms) {
    try { const v = await fn(); if (v) return v; ultimo = v; } catch (e) { ultimo = e.message; }
    await sleep(100);
  }
  throw new Error(`Tiempo agotado esperando: ${desc} (último valor: ${JSON.stringify(ultimo)})`);
}
function comprobar(condicion, mensaje) { if (!condicion) throw new Error(mensaje); }

/* --- Procesos: servidor simulado y navegador --- */
const datos = mkdtempSync(join(tmpdir(), 'tpv_navegador_'));
const perfil = mkdtempSync(join(tmpdir(), 'tpv_chrome_'));
const servidor = spawn('python', [join(AQUI, 'servidor_simulado.py'), String(PUERTO), datos], { stdio: ['ignore', 'pipe', 'pipe'] });
let logServidor = '';
servidor.stdout.on('data', d => logServidor += d);
servidor.stderr.on('data', d => logServidor += d);
const navegador = spawn(NAVEGADOR, ['--headless=new', `--remote-debugging-port=${PUERTO_CDP}`, `--user-data-dir=${perfil}`,
  '--window-size=1366,768', '--no-first-run', '--no-default-browser-check', 'about:blank'], { stdio: 'ignore' });

async function api(ruta, body) {
  const opciones = body === undefined ? {} : { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) };
  return (await fetch(BASE + ruta, opciones)).json();
}
const trabajos = () => api('/__pruebas/trabajos');

/* --- Cliente mínimo del protocolo de depuración de Chrome --- */
let ws, siguienteId = 0;
const pendientes = new Map();
const erroresJS = [];
const dialogos = [];

function cdp(method, params = {}) {
  const id = ++siguienteId;
  ws.send(JSON.stringify({ id, method, params }));
  return new Promise((ok, ko) => pendientes.set(id, { ok, ko }));
}

async function conectar() {
  const pagina = await esperar(async () =>
    (await (await fetch(`http://127.0.0.1:${PUERTO_CDP}/json/list`)).json()).find(t => t.type === 'page'), 'navegador', 15000);
  ws = new WebSocket(pagina.webSocketDebuggerUrl);
  await new Promise((ok, ko) => { ws.onopen = ok; ws.onerror = ko; });
  ws.onmessage = ev => {
    const m = JSON.parse(ev.data);
    if (m.id && pendientes.has(m.id)) {
      const { ok, ko } = pendientes.get(m.id);
      pendientes.delete(m.id);
      m.error ? ko(new Error(m.error.message)) : ok(m.result);
    } else if (m.method === 'Runtime.exceptionThrown') {
      const d = m.params.exceptionDetails;
      erroresJS.push(d.exception?.description || d.text);
    } else if (m.method === 'Runtime.consoleAPICalled' && m.params.type === 'error') {
      erroresJS.push('console.error: ' + m.params.args.map(a => a.value ?? a.description).join(' '));
    } else if (m.method === 'Page.javascriptDialogOpening') {
      dialogos.push(m.params.message);
      cdp('Page.handleJavaScriptDialog', { accept: true });
    }
  };
  await cdp('Runtime.enable');
  await cdp('Page.enable');
}

async function js(expresion) {
  const r = await cdp('Runtime.evaluate', { expression: expresion, awaitPromise: true, returnByValue: true });
  if (r.exceptionDetails) throw new Error('Error JS: ' + (r.exceptionDetails.exception?.description || r.exceptionDetails.text));
  return r.result.value;
}

// Pulsa el botón visible cuyo texto contiene `texto` dentro de `ambito`
const pulsar = (texto, ambito = 'body', exacto = false) => js(`(() => {
  const b = [...document.querySelectorAll(${JSON.stringify(ambito + ' button')})].find(b =>
    b.getClientRects().length > 0 && !b.disabled &&
    (${exacto} ? b.textContent.trim() === ${JSON.stringify(texto)} : b.textContent.includes(${JSON.stringify(texto)})));
  if (!b) throw new Error('No hay botón visible: ' + ${JSON.stringify(texto)});
  b.click(); return true;
})()`);
const pulsarId = id => js(`document.getElementById(${JSON.stringify(id)}).click()`);
const pulsarProducto = nombre => js(`(() => {
  const b = [...document.querySelectorAll('.prod-btn')].find(b => b.querySelector('.pname').textContent === ${JSON.stringify(nombre)});
  if (!b) throw new Error('Producto no encontrado: ${nombre}'); b.click(); return true;
})()`);
const abierto = id => js(`document.getElementById(${JSON.stringify(id)}).classList.contains('open')`);
const textoDe = id => js(`document.getElementById(${JSON.stringify(id)}).textContent`);
const toastActual = () => textoDe('toast');
const esperarToast = parte => esperar(async () => (await toastActual()).includes(parte), `aviso "${parte}"`);

async function nuevosTrabajos(antes, cuantos) {
  return esperar(async () => {
    const t = await trabajos();
    return t.length - antes >= cuantos && t.slice(antes);
  }, `${cuantos} trabajo(s) de impresión`);
}

async function cobrarExacto() {
  await pulsarId('btn-cobrar');
  await pulsar('Exacto', '#pay-overlay');
  await pulsarId('btn-pay-confirm');
}

async function captura(nombre) {
  if (!CAPTURAS) return;
  const { data } = await cdp('Page.captureScreenshot', { format: 'png' });
  writeFileSync(join(CAPTURAS, nombre + '.png'), Buffer.from(data, 'base64'));
}

/* --- Pruebas --- */
const resultados = [];
async function prueba(nombre, fn) {
  // Cada prueba empieza con la pantalla limpia
  await js(`document.querySelectorAll('.modal-overlay.open, .vkb-overlay.open').forEach(o => o.classList.remove('open')); clearOrder();`);
  try {
    await fn();
    resultados.push(true);
    console.log(`  OK     ${nombre}`);
  } catch (e) {
    resultados.push(false);
    console.log(`  FALLO  ${nombre}\n         ${e.message}`);
  }
}

async function main() {
  await esperar(async () => (await fetch(BASE + '/api/productos')).ok, 'servidor simulado', 20000);
  await conectar();
  await cdp('Emulation.setDeviceMetricsOverride', { width: 1366, height: 768, deviceScaleFactor: 1, mobile: false });
  await cdp('Page.navigate', { url: BASE });
  await esperar(() => js(`document.querySelectorAll('.prod-btn').length > 0`), 'productos cargados');
  // window.print abriría el diálogo de impresión: se cuenta en lugar de abrirlo
  await js(`window.__impresiones = 0; window.print = () => { window.__impresiones++; }`);

  console.log(`\nPruebas en navegador (${NAVEGADOR.split('/').pop()})\n`);

  await prueba('La página carga sin errores y el indicador de impresora está en verde', async () => {
    await esperar(async () => (await js(`document.getElementById('printer-status').className`)) === 'printer-status ok', 'indicador verde');
    comprobar((await js(`document.getElementById('printer-status').title`)).includes('TM-T20'), 'el indicador no muestra el nombre');
  });

  await prueba('Cobro en efectivo con botón rápido de 20 €: guarda, abre el cajón e imprime el ticket', async () => {
    const antes = (await trabajos()).length;
    await pulsarProducto('Pollo asado');
    await pulsarId('btn-cobrar');
    await pulsar('20 €', '#pay-overlay');
    comprobar((await textoDe('pay-cash-ef')) === '20', 'el botón de 20 € no pone el importe');
    comprobar((await textoDe('pay-change-ef')).includes('+9.00'), 'cambio incorrecto: ' + await textoDe('pay-change-ef'));
    await captura('1_cobro_efectivo');
    await pulsarId('btn-pay-confirm');
    await esperar(() => abierto('receipt-overlay'), 'ticket en pantalla');
    await esperarToast('Venta registrada');
    comprobar((await js('order.length')) === 0, 'el pedido no se ha vaciado');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.abre_cajon, 'no se ha abierto el cajón');
    for (const s of ['1x Pollo asado', '11.00 €', 'Efectivo', '20.00 €', 'Cambio', '9.00 €']) comprobar(t.texto.includes(s), 'falta en el ticket: ' + s);
    await captura('2_ticket');
  });

  await prueba('Botón "Exacto" pone el total y deja cambio 0', async () => {
    await pulsarProducto('Pollo asado');
    await pulsarProducto('Medio pollo frito');
    await pulsarId('btn-cobrar');
    await pulsar('Exacto', '#pay-overlay');
    comprobar((await textoDe('pay-cash-ef')) === '17.50', 'importe exacto incorrecto: ' + await textoDe('pay-cash-ef'));
    comprobar((await textoDe('pay-change-ef')).includes('+0.00'), 'cambio distinto de 0');
    comprobar(!(await js(`document.getElementById('btn-pay-confirm').disabled`)), 'no deja confirmar');
  });

  await prueba('Cobro con tarjeta: imprime ticket sin abrir el cajón', async () => {
    const antes = (await trabajos()).length;
    await pulsarProducto('Pollo asado');
    await pulsarId('btn-cobrar');
    await pulsar('Tarjeta', '#pay-overlay .pay-tabs');
    await pulsarId('btn-pay-confirm');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(!t.abre_cajon, 'se ha abierto el cajón con tarjeta');
    comprobar(t.texto.includes('Tarjeta'), 'el ticket no indica tarjeta');
  });

  await prueba('Cobro mixto con parte en efectivo: abre el cajón', async () => {
    const antes = (await trabajos()).length;
    await pulsarProducto('Pollo asado');
    await pulsarId('btn-cobrar');
    await pulsar('Mixto', '#pay-overlay .pay-tabs');
    await pulsar('5', '#pay-np-mx', true);
    comprobar((await textoDe('pay-card-mx')).includes('6.00'), 'parte de tarjeta incorrecta');
    await pulsarId('btn-pay-confirm');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.abre_cajon, 'no se ha abierto el cajón en el pago mixto');
  });

  await prueba('Pulsar Confirmar varias veces seguidas cobra una sola vez', async () => {
    const ventasAntes = (await api('/api/ventas')).length;
    await pulsarProducto('Pollo asado');
    await pulsarId('btn-cobrar');
    await pulsar('Exacto', '#pay-overlay');
    await js(`document.getElementById('btn-pay-confirm').click(); confirmPay(); confirmPay();`);
    await esperar(() => abierto('receipt-overlay'), 'ticket en pantalla');
    await sleep(500);
    comprobar((await api('/api/ventas')).length === ventasAntes + 1, 'se ha registrado más de una venta');
  });

  await prueba('Si el servidor no puede guardar la venta: no se pierde el pedido ni se abre el cajón', async () => {
    const antes = (await trabajos()).length;
    await api('/__pruebas/ventas_rotas', { rotas: true });
    await pulsarProducto('Pollo asado');
    await cobrarExacto();
    await esperarToast('NO se ha guardado');
    comprobar(await abierto('pay-overlay'), 'se ha cerrado la ventana de cobro');
    comprobar(!(await abierto('receipt-overlay')), 'se ha mostrado un ticket de una venta no guardada');
    comprobar((await js('order.length')) === 1, 'se ha borrado el pedido');
    comprobar((await trabajos()).length === antes, 'se ha impreso o abierto el cajón');
    comprobar(!(await js(`document.getElementById('btn-pay-confirm').disabled`)), 'no se puede reintentar');
    await captura('3_error_venta');
    // Reintento cuando el servidor vuelve a funcionar
    await api('/__pruebas/ventas_rotas', { rotas: false });
    await pulsarId('btn-pay-confirm');
    await esperar(() => abierto('receipt-overlay'), 'ticket tras reintentar');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.abre_cajon, 'no se abre el cajón al reintentar');
  });

  await prueba('Si el servidor está caído: aviso y el pedido se mantiene', async () => {
    await pulsarProducto('Pollo asado');
    await pulsarId('btn-cobrar');
    await pulsar('Exacto', '#pay-overlay');
    await js(`window.__fetchReal = window.fetch; window.fetch = () => Promise.reject(new TypeError('Failed to fetch'));`);
    try {
      await pulsarId('btn-pay-confirm');
      await esperarToast('Sin conexión con el servidor');
      comprobar((await js('order.length')) === 1, 'se ha borrado el pedido');
      comprobar(await abierto('pay-overlay'), 'se ha cerrado la ventana de cobro');
    } finally {
      await js(`window.fetch = window.__fetchReal;`);
    }
  });

  await prueba('Botón "Abrir Cajón" del menú', async () => {
    const antes = (await trabajos()).length;
    await pulsar('Abrir Cajón', '#topbar');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.solo_cajon, 'se ha enviado algo más que la apertura del cajón');
    await esperarToast('Cajón abierto');
  });

  await prueba('Reimprimir el ticket desde la pantalla del ticket (sin abrir el cajón)', async () => {
    await pulsarProducto('Pollo asado');
    await cobrarExacto();
    await esperar(() => abierto('receipt-overlay'), 'ticket en pantalla');
    const antes = (await trabajos()).length;
    await pulsar('Imprimir', '#receipt-overlay');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(!t.abre_cajon && t.texto.includes('TOTAL (IVA incl.)'), 'reimpresión incorrecta');
    await esperarToast('Ticket enviado');
    comprobar((await js('window.__impresiones')) === 0, 'se ha abierto la impresión del navegador');
  });

  await prueba('Comanda directa a cocina, agrupada por categoría y sin precios', async () => {
    const antes = (await trabajos()).length;
    await pulsarProducto('Pollo asado');
    await pulsarProducto('Pollo asado');
    await js(`order[0].removals = ['Sin salsa']; order[0].notes = 'Muy hecho'; renderOrder();`);
    await pulsarId('btn-comanda');
    const [t] = await nuevosTrabajos(antes, 1);
    for (const s of ['COMANDA COCINA', '---- POLLOS ----', '2x Pollo asado', '- Sin salsa', 'Nota: Muy hecho']) comprobar(t.texto.includes(s), 'falta en la comanda: ' + s);
    comprobar(!t.texto.includes('€'), 'la comanda lleva precios');
    comprobar(!(await abierto('receipt-overlay')), 'se ha abierto la ventana del navegador');
    comprobar((await js('order.length')) === 1, 'la comanda ha vaciado el pedido');
  });

  await prueba('Sin impresora configurada: todo funciona y se imprime desde el navegador como antes', async () => {
    await api('/__pruebas/config', { impresora: '' });
    try {
      await js('actualizarEstadoImpresora()');
      await esperar(async () => (await js(`document.getElementById('printer-status').className`)) === 'printer-status ', 'indicador gris');
      const antes = (await trabajos()).length;
      const impresiones = await js('window.__impresiones');
      await pulsarProducto('Pollo asado');
      await pulsarId('btn-comanda');
      await esperar(async () => (await js('window.__impresiones')) === impresiones + 1, 'impresión de la comanda por el navegador');
      comprobar(await abierto('receipt-overlay'), 'no se muestra la comanda');
      await pulsar('Cerrar', '#receipt-overlay');
      await cobrarExacto();
      await esperarToast('Impresora no configurada');
      comprobar(await abierto('receipt-overlay'), 'la venta no muestra el ticket');
      await pulsar('Imprimir', '#receipt-overlay');
      await esperar(async () => (await js('window.__impresiones')) === impresiones + 2, 'impresión del ticket por el navegador');
      await pulsar('Abrir Cajón', '#topbar');
      await esperarToast('Impresora no configurada');
      comprobar((await trabajos()).length === antes, 'se ha enviado algo a la impresora');
      comprobar((await api('/api/ventas')).length > 0, 'la venta no se ha guardado');
    } finally {
      await api('/__pruebas/config', { impresora: 'TM-T20' });
      await js('actualizarEstadoImpresora()');
    }
  });

  await prueba('Anular una venta en efectivo desde "Ventas"', async () => {
    const antes = (await trabajos()).length;
    await pulsar('Ventas', '#topbar');
    await esperar(() => abierto('ventas-overlay'), 'ventana de ventas');
    const resumenAntes = await js(`document.querySelector('#ventas-summary .vs-val').textContent`);
    const dialogosAntes = dialogos.length;
    await pulsar('Anular', '#ventas-list');
    await esperarToast('Venta anulada');
    comprobar(dialogos.length === dialogosAntes + 1 && dialogos.at(-1).includes('Se abrirá el cajón'), 'no se ha pedido confirmación');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.abre_cajon, 'no se abre el cajón para devolver el dinero');
    comprobar(t.texto.includes('ANULACION DE VENTA') && t.texto.includes('Devuelto en efectivo'), 'ticket de anulación incorrecto');
    await esperar(async () => (await textoDe('ventas-list')).includes('(ANULADA)'), 'venta marcada como anulada');
    comprobar((await textoDe('ventas-list')).includes('anulación'), 'no aparece la fila de anulación');
    const resumenDespues = await js(`document.querySelector('#ventas-summary .vs-val').textContent`);
    comprobar(Number(resumenDespues) === Number(resumenAntes) - 1, `el contador de ventas no baja (${resumenAntes} -> ${resumenDespues})`);
    await captura('4_ventas_anulada');
  });

  await prueba('Dos ventas en el mismo segundo: anular una solo marca una', async () => {
    const { timestamp } = await api('/__pruebas/ventas_mismo_segundo', {});
    await pulsar('Ventas', '#topbar');
    await esperar(() => abierto('ventas-overlay'), 'ventana de ventas');
    const indice = await js(`ventasCache.findIndex(v => v.timestamp === ${JSON.stringify(timestamp)})`);
    await js(`anularVenta(${indice})`);
    await esperarToast('Venta anulada');
    await esperar(async () => (await js(`ventasCache.filter(v => v.anula_a === ${JSON.stringify(timestamp)}).length`)) === 1, 'lista actualizada');
    const filas = await js(`[...document.querySelectorAll('#ventas-list tr')].map(tr => tr.textContent).filter(t => t.includes('Venta gemela') && !t.includes('anulación'))`);
    comprobar(filas.length === 2, 'no aparecen las dos ventas');
    comprobar(filas.filter(t => t.includes('(ANULADA)')).length === 1, 'se marcan como anuladas ' + filas.filter(t => t.includes('(ANULADA)')).length + ' ventas');
    comprobar(filas.filter(t => t.includes('Anular')).length === 1, 'la otra venta no se puede anular');
  });

  await prueba('Ver el ticket de una anulación', async () => {
    await pulsar('Ventas', '#topbar');
    await esperar(() => abierto('ventas-overlay'), 'ventana de ventas');
    await js(`verTicketAntiguo(ventasCache.findIndex(v => v.metodo_pago === 'anulacion'))`);
    const ticket = await textoDe('receipt-content');
    comprobar(ticket.includes('ANULACIÓN DE VENTA') && ticket.includes('Devuelto en efectivo'), 'ticket de anulación en pantalla incorrecto');
  });

  await prueba('Salida de caja con teclado numérico y teclado virtual', async () => {
    await pulsar('Entradas/Salidas', '#topbar');
    await esperar(() => abierto('mov-overlay'), 'ventana de movimientos');
    // Validación: sin datos no se registra nada
    const antes = (await trabajos()).length;
    await pulsar('Registrar', '#mov-overlay');
    await esperarToast('Introduce el importe');
    comprobar((await trabajos()).length === antes, 'se ha abierto el cajón sin registrar nada');

    await pulsar('Salida de dinero', '#mov-overlay');
    await pulsarId('mov-importe');
    for (const tecla of ['1', '2', '.', '5']) await pulsar(tecla, '#numpad-overlay', true);
    await pulsar('Aceptar', '#numpad-overlay');
    comprobar((await js(`document.getElementById('mov-importe').value`)) === '12.50', 'importe mal introducido');
    await pulsarId('mov-concepto');
    for (const tecla of ['h', 'i', 'e', 'l', 'o']) await pulsar(tecla, '#vkb-overlay', true);
    await pulsar('Aceptar', '#vkb-overlay');
    await captura('5_movimiento');
    await pulsar('Registrar', '#mov-overlay');
    await esperarToast('Salida registrada');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.solo_cajon, 'no se ha abierto el cajón');
    await esperar(async () => (await textoDe('mov-list')).includes('hielo'), 'movimiento en la lista');
    comprobar((await textoDe('mov-list')).includes('-12.50 €'), 'importe de la lista incorrecto');
    comprobar((await textoDe('mov-resumen')).includes('12.50 €'), 'resumen de salidas incorrecto');
    comprobar((await js(`document.getElementById('mov-importe').value`)) === '', 'el formulario no se ha limpiado');
  });

  await prueba('Cierre de caja: abre el cajón, cuenta las salidas e imprime el justificante', async () => {
    const antes = (await trabajos()).length;
    await pulsar('Cierre de Caja', '#topbar');
    await esperar(() => abierto('cierre-overlay'), 'ventana de cierre');
    const [apertura] = await nuevosTrabajos(antes, 1);
    comprobar(apertura.solo_cajon, 'no se abre el cajón al empezar el cierre');

    const ventas = await api('/api/ventas');
    const efectivo = ventas.reduce((s, v) => s + (parseFloat(v.pagado_efectivo) - parseFloat(v.cambio)), 0);
    const esperado = (50 + efectivo - 12.5).toFixed(2) + ' €';
    comprobar((await textoDe('c-salidas')) === '12.50 €', 'salidas en el cierre: ' + await textoDe('c-salidas'));
    comprobar((await textoDe('c-teorico')) === esperado, `teórico ${await textoDe('c-teorico')} en vez de ${esperado}`);
    await js(`document.getElementById('c-20').value = 2; calcCierre();`);
    await captura('6_cierre');
    await pulsar('Guardar Cierre', '#cierre-overlay');
    await esperarToast('Cierre de caja guardado');
    const t = (await nuevosTrabajos(antes, 2))[1];
    for (const s of ['CIERRE DE CAJA', '- Salidas de caja', '12.50 €', 'hielo', '2 x 20 EUR', '40.00 €', 'Teorico en caja', esperado])
      comprobar(t.texto.includes(s), 'falta en el cierre: ' + s);
    comprobar(!(await abierto('cierre-overlay')), 'la ventana de cierre sigue abierta');
  });

  await prueba('Cobro de un pedido telefónico en espera', async () => {
    const { pedido } = await api('/api/pedido', {
      telefono: '600111222', nombre_cliente: 'Ana', total: '11.00',
      items: [{ id: 3, nombre: 'Pollo asado', precioBase: 11, qty: 1, removals: [], extras: [], notes: '' }]
    });
    const antes = (await trabajos()).length;
    await js(`cargarPedido(${pedido.id})`);
    await cobrarExacto();
    await esperarToast('Pedido cobrado');
    const [t] = await nuevosTrabajos(antes, 1);
    comprobar(t.abre_cajon, 'no se abre el cajón');
    comprobar(!(await api('/api/pedidos')).some(p => p.id === pedido.id), 'el pedido sigue pendiente');
  });

  await prueba('El indicador de impresora avisa de falta de papel y de trabajos atascados', async () => {
    try {
      await api('/__pruebas/impresora', { Status: 0x10 });
      await js('actualizarEstadoImpresora()');
      await esperar(async () => (await js(`document.getElementById('printer-status').className`)) === 'printer-status error', 'indicador rojo');
      comprobar((await js(`document.getElementById('printer-status').title`)).includes('Sin papel'), 'no dice "Sin papel"');
      await js('mostrarEstadoImpresora()');
      await esperarToast('Sin papel');
      await api('/__pruebas/impresora', { Status: 0, cJobs: 4 });
      await js('actualizarEstadoImpresora()');
      await esperar(async () => (await js(`document.getElementById('printer-status').className`)) === 'printer-status aviso', 'indicador amarillo');
    } finally {
      await api('/__pruebas/impresora', { Status: 0, cJobs: 0 });
      await js('actualizarEstadoImpresora()');
    }
  });

  for (const [ancho, alto] of [[1024, 768], [1366, 768]]) {
    await prueba(`En ${ancho}x${alto} los botones del cobro se ven sin desplazar la ventana`, async () => {
      await cdp('Emulation.setDeviceMetricsOverride', { width: ancho, height: alto, deviceScaleFactor: 1, mobile: false });
      await pulsarProducto('Pollo asado');
      await pulsarId('btn-cobrar');
      for (const pestana of ['Efectivo', 'Mixto']) {
        await pulsar(pestana, '#pay-overlay .pay-tabs');
        await sleep(100);
        const fuera = await js(`(() => {
          const modal = document.querySelector('#pay-overlay .modal');
          const limite = Math.min(window.innerHeight, modal.getBoundingClientRect().bottom);
          return ['btn-pay-confirm', 'btn-pay-cancel'].filter(id => document.getElementById(id).getBoundingClientRect().bottom > limite + 0.5);
        })()`);
        await captura(`cobro_${pestana}_${ancho}x${alto}`);
        comprobar(fuera.length === 0, `pestaña ${pestana}: botones cortados: ${fuera.join(', ')}`);
      }
    });
  }

  for (const ancho of [1024, 1280, 1366, 1920]) {
    await prueba(`La barra superior cabe entera en una pantalla de ${ancho} px`, async () => {
      await cdp('Emulation.setDeviceMetricsOverride', { width: ancho, height: 768, deviceScaleFactor: 1, mobile: false });
      await sleep(200);
      const fuera = await js(`[...document.querySelectorAll('#topbar > *')]
        .filter(e => e.getBoundingClientRect().right > window.innerWidth + 0.5 || e.getBoundingClientRect().width < 8)
        .map(e => e.textContent.trim())`);
      await captura(`barra_${ancho}`);
      comprobar(fuera.length === 0, 'no caben: ' + fuera.join(', '));
      const tituloCortado = await js(`(() => { const t = document.querySelector('#topbar .title'); return t.scrollWidth > t.clientWidth; })()`);
      comprobar(!tituloCortado, 'el nombre del local aparece cortado');
    });
  }

  comprobar(true);
  if (erroresJS.length) {
    resultados.push(false);
    console.log('  FALLO  Errores de JavaScript en la página:\n         ' + erroresJS.join('\n         '));
  } else {
    console.log('  OK     Ningún error de JavaScript en la página');
  }
}

let codigo = 1;
try {
  await main();
  const fallos = resultados.filter(r => !r).length;
  console.log(`\n${resultados.length - fallos} correctas, ${fallos} fallidas`);
  codigo = fallos ? 1 : 0;
} catch (e) {
  console.log('ERROR: ' + e.stack + '\n--- Servidor ---\n' + logServidor);
} finally {
  try { ws?.close(); } catch {}
  navegador.kill();
  servidor.kill();
  await sleep(800);
  for (const d of [perfil, datos]) { try { rmSync(d, { recursive: true, force: true }); } catch {} }
  process.exit(codigo);
}
