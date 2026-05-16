#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TPV - Terminal Punto de Venta
Servidor Flask + Frontend táctil integrado
"""

import csv, os, io, json
from datetime import datetime
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
CSV_PROD   = os.path.join(BASE_DIR, "productos.csv")
CSV_VENTAS = os.path.join(BASE_DIR, "ventas.csv")

# --- Utilidades CSV ---
def leer_productos():
    productos = []
    if not os.path.exists(CSV_PROD):
        return productos
    with open(CSV_PROD, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["id"]     = int(row["id"])
            row["precio"] = float(row["precio"])
            productos.append(row)
    return productos

def guardar_productos(productos):
    with open(CSV_PROD, "w", newline="", encoding="utf-8") as f:F
    writer = csv.DictWriter(f, fieldnames=["id","nombre","precio","categoria"])
    writer.writeheader()
    for p in productos:
          writer.writerow(p)

def siguiente_id(productos):
    if not productos:
        return 1
    return max(p["id"] for p in productos) + 1

def registrar_venta(data):
    es_nuevo = not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0
    with open(CSV_VENTAS, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if es_nuevo:
            writer.writerow(["timestamp","items_json","total","pagado_efectivo","pagado_tarjeta","cambio","metodo_pago"])
        writer.writerow([
            data.get("timestamp", ""),
            json.dumps(data.get("items", []), ensure_ascii=False),
            data.get("total", "0.00"),
            data.get("pagado_efectivo", "0.00"),
            data.get("pagado_tarjeta", "0.00"),
            data.get("cambio", "0.00"),
            data.get("metodo_pago", "efectivo")
        ])

def leer_ventas_hoy():
    ventas = []
    if not os.path.exists(CSV_VENTAS) or os.path.getsize(CSV_VENTAS) == 0:
        return ventas
    hoy = datetime.now().strftime("%Y-%m-%d")
    with open(CSV_VENTAS, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp", "")
            if ts and ts.startswith(hoy):
                try:
                    row["items"] = json.loads(row.get("items_json", "[]"))
                except:
                    row["items"] = []
                row.pop("items_json", None)
                ventas.append(row)
    return ventas

# --- API ---
@app.route("/api/productos", methods=["GET"])
def api_get_productos():
    return jsonify(leer_productos())

@app.route("/api/productos", methods=["POST"])
def api_add_producto():
    data = request.json
    productos = leer_productos()
    nuevo = {
        "id":        siguiente_id(productos),
        "nombre":    data["nombre"],
        "precio":    round(float(data["precio"]), 2),
        "categoria": data["categoria"]
    }
    productos.append(nuevo)
    guardar_productos(productos)
    return jsonify(nuevo), 201

@app.route("/api/productos/<int:pid>", methods=["PUT"])
def api_update_producto(pid):
    data = request.json
    productos = leer_productos()
    for p in productos:
        if p["id"] == pid:
            if "nombre"    in data: p["nombre"]    = data["nombre"]
            if "precio"    in data: p["precio"]    = round(float(data["precio"]), 2)
            if "categoria" in data: p["categoria"] = data["categoria"]
            guardar_productos(productos)
            return jsonify(p)
    return jsonify({"error": "Producto no encontrado"}), 404

@app.route("/api/productos/<int:pid>", methods=["DELETE"])
def api_delete_producto(pid):
    productos = leer_productos()
    productos = [p for p in productos if p["id"] != pid]
    guardar_productos(productos)
    return jsonify({"ok": True})

@app.route("/api/venta", methods=["POST"])
def api_registrar_venta():
    data = request.json
    data["timestamp"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    registrar_venta(data)
    return jsonify({"ok": True, "timestamp": data["timestamp"]}), 201

@app.route("/api/ventas", methods=["GET"])
def api_get_ventas():
    return jsonify(leer_ventas_hoy())

# --- Frontend ---
@app.route("/")
def index():
    return Response(HTML_PAGE, mimetype="text/html")

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1,user-scalable=no">
<title>TPV - Pollería Las Portadas</title>
<style>
/* == RESET & BASE == */
*{margin:0;padding:0;box-sizing:border-box}
html,body{height:100%;font-family:'Segoe UI',Tahoma,Arial,sans-serif;overflow:hidden;background:#f0f2f5;
  -webkit-touch-callout:none;-webkit-user-select:none;user-select:none}
button{cursor:pointer;border:none;outline:none;font-family:inherit;-webkit-tap-highlight-color:transparent}
::-webkit-scrollbar{width:8px}
::-webkit-scrollbar-thumb{background:#bbb;border-radius:4px}

/* == LAYOUT == */
#app{display:grid;grid-template-rows:56px 1fr;grid-template-columns:1fr 400px;height:100vh}
#topbar{grid-column:1/3;background:#1e293b;color:#fff;display:flex;align-items:center;
  padding:0 20px;gap:16px;font-size:18px;z-index:10}
#topbar .title{font-weight:700;font-size:20px;flex:1}
#topbar .clock{font-size:20px;font-variant-numeric:tabular-nums;margin-right:8px}
#topbar button{background:rgba(255,255,255,.15);color:#fff;border-radius:8px;padding:8px 14px;
  font-size:15px;transition:.15s}
#topbar button:active{background:rgba(255,255,255,.3)}
#left{grid-column:1;grid-row:2;display:flex;flex-direction:column;overflow:hidden}
#right{grid-column:2;grid-row:2;background:#fff;display:flex;flex-direction:column;
  border-left:2px solid #e2e8f0;overflow:hidden}

/* == CATEGORY TABS == */
#cat-tabs{display:flex;gap:6px;padding:10px 12px 6px;background:#f8fafc;flex-wrap:wrap}
.cat-tab{padding:10px 18px;border-radius:10px;font-size:15px;font-weight:600;
  background:#e2e8f0;color:#475569;transition:.15s;min-height:48px}
.cat-tab.active{background:#3b82f6;color:#fff}
.cat-tab:active{transform:scale(.95)}

/* == SUBCATEGORY TABS == */
#subcat-tabs { display: flex; gap: 6px; padding: 0 12px 8px; background: #f8fafc; flex-wrap: wrap; }
.subcat-tab { padding: 6px 14px; border-radius: 8px; font-size: 13px; font-weight: 600; background: #e2e8f0; color: #475569; transition: .15s; border: 1px solid #cbd5e1; }
.subcat-tab.active { background: #0ea5e9; color: #fff; border-color: #0284c7; }
.subcat-tab:active { transform: scale(.95); }

/* == PRODUCT GRID == */
#prod-grid{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch;padding:10px;
  display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;
  align-content:start}
.prod-btn{background:#fff;border:2px solid #e2e8f0;border-radius:14px;padding:14px 10px;
  display:flex;flex-direction:column;align-items:center;justify-content:center;
  min-height:110px;transition:.15s;gap:6px}
.prod-btn:active{transform:scale(.95);border-color:#3b82f6;background:#eff6ff}
.prod-btn .pname{font-size:15px;font-weight:600;text-align:center;color:#1e293b;line-height:1.2}
.prod-btn .pprice{font-size:20px;font-weight:700;color:#16a34a}

/* == ORDER PANEL (RIGHT) == */
#order-header{padding:14px 16px;background:#f1f5f9;font-size:18px;font-weight:700;
  color:#1e293b;border-bottom:1px solid #e2e8f0;display:flex;align-items:center}
#order-header span{flex:1}
#order-list{flex:1;overflow-y:auto;-webkit-overflow-scrolling:touch}
.order-item{display:grid;grid-template-columns:1fr auto auto auto;align-items:center;
  padding:10px 14px;border-bottom:1px solid #f1f5f9;gap:8px}
.order-item .oi-info{display:flex;flex-direction:column;gap:2px;cursor:pointer}
.order-item .oi-name{font-size:15px;font-weight:600;color:#334155}
.order-item .oi-mods{font-size:12px;color:#94a3b8;line-height:1.3}
.order-item .oi-qty{display:flex;align-items:center;gap:4px}
.order-item .oi-qty button{width:36px;height:36px;border-radius:8px;font-size:20px;font-weight:700}
.oi-minus{background:#fee2e2;color:#dc2626}
.oi-minus:active{background:#dc2626;color:#fff}
.oi-plus{background:#dcfce7;color:#16a34a}
.oi-plus:active{background:#16a34a;color:#fff}
.oi-qval{font-size:17px;font-weight:700;min-width:28px;text-align:center}
.order-item .oi-sub{font-size:16px;font-weight:700;color:#1e293b;min-width:68px;text-align:right}
.order-item .oi-del{width:36px;height:36px;border-radius:8px;background:#fef2f2;color:#ef4444;
  font-size:18px}
.order-item .oi-del:active{background:#ef4444;color:#fff}
#order-empty{text-align:center;padding:40px 20px;color:#94a3b8;font-size:16px}
#order-footer{padding:14px 16px;border-top:2px solid #e2e8f0;background:#f8fafc}
#order-total{font-size:28px;font-weight:800;color:#1e293b;text-align:right;margin-bottom:12px}
#order-actions{display:flex;gap:10px}
#order-actions button{flex:1;padding:16px;border-radius:12px;font-size:17px;font-weight:700;
  min-height:56px;transition:.15s}
#order-actions button:active{transform:scale(.97)}
#btn-cobrar{background:#16a34a;color:#fff}
#btn-cobrar:disabled{background:#86efac;color:#fff}
#btn-limpiar{background:#ef4444;color:#fff}

/* == MODALS == */
.modal-overlay{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;
  justify-content:center;align-items:center;z-index:100}
.modal-overlay.open{display:flex}
.modal{background:#fff;border-radius:20px;max-width:95vw;max-height:95vh;overflow-y:auto;
  -webkit-overflow-scrolling:touch;box-shadow:0 20px 60px rgba(0,0,0,.3)}

/* == NUMPAD == */
.numpad-modal { z-index: 150; }
.numpad-modal .modal{width:340px;padding:20px}
.numpad-title{font-size:16px;color:#64748b;text-align:center;margin-bottom:8px}
.numpad-display{background:#f1f5f9;border-radius:12px;padding:16px;font-size:32px;
  font-weight:700;text-align:right;margin-bottom:14px;min-height:60px;color:#1e293b;
  word-break:break-all}
.numpad-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}
.numpad-grid button{height:64px;border-radius:12px;font-size:24px;font-weight:700;
  background:#f1f5f9;color:#1e293b;transition:.12s}
.numpad-grid button:active{background:#cbd5e1}
.numpad-grid .np-confirm{background:#16a34a;color:#fff}
.numpad-grid .np-confirm:active{background:#15803d}
.numpad-grid .np-clear{background:#fef2f2;color:#ef4444}
.numpad-grid .np-back{background:#fef9c3;color:#a16207}

/* == PAYMENT MODAL == */
.pay-modal .modal{width:460px;padding:24px}
.pay-total-label{font-size:16px;color:#64748b;text-align:center}
.pay-total{font-size:38px;font-weight:800;text-align:center;color:#1e293b;margin:4px 0 14px}
.pay-tabs{display:flex;gap:6px;margin-bottom:16px}
.pay-tab{flex:1;padding:12px 6px;border-radius:10px;font-size:14px;font-weight:700;
  background:#e2e8f0;color:#475569;text-align:center;min-height:48px;transition:.15s}
.pay-tab.active{color:#fff}
.pay-tab.tab-efectivo.active{background:#16a34a}
.pay-tab.tab-tarjeta.active{background:#3b82f6}
.pay-tab.tab-mixto.active{background:#f59e0b}
.pay-tab:active{transform:scale(.97)}
.pay-section{display:none}
.pay-section.active{display:block}
.pay-field-label{font-size:14px;color:#64748b;margin-bottom:4px;margin-top:8px}
.pay-cash-display{background:#fffbeb;border:2px solid #fcd34d;border-radius:12px;padding:14px;
  font-size:26px;font-weight:700;text-align:right;margin-bottom:6px;min-height:50px;color:#92400e}
.pay-card-display{background:#eff6ff;border:2px solid #93c5fd;border-radius:12px;padding:14px;
  font-size:26px;font-weight:700;text-align:right;margin-bottom:6px;min-height:50px;color:#1e40af}
.pay-change{font-size:20px;font-weight:700;text-align:center;margin:10px 0;padding:10px;border-radius:10px}
.pay-change.positive{background:#dcfce7;color:#16a34a}
.pay-change.negative{background:#fef2f2;color:#ef4444}
.pay-change.zero{background:#f1f5f9;color:#64748b}
.pay-numpad{display:grid;grid-template-columns:repeat(3,1fr);gap:6px;margin-bottom:10px}
.pay-numpad button{height:56px;border-radius:10px;font-size:22px;font-weight:700;
  background:#f1f5f9;color:#1e293b;transition:.12s}
.pay-numpad button:active{background:#cbd5e1}
.pay-numpad .np-clear{background:#fef2f2;color:#ef4444}
.pay-numpad .np-back{background:#fef9c3;color:#a16207}
.pay-card-info{text-align:center;padding:20px;font-size:18px;color:#475569}
.pay-card-info .big-icon{font-size:60px;display:block;margin-bottom:10px}
.pay-actions{display:flex;gap:10px;margin-top:12px}
.pay-actions button{flex:1;padding:14px;border-radius:12px;font-size:16px;font-weight:700;min-height:54px}
#btn-pay-confirm{background:#16a34a;color:#fff}
#btn-pay-confirm:disabled{background:#86efac}
#btn-pay-cancel{background:#ef4444;color:#fff}

/* == EDIT ITEM MODAL == */
.edit-modal .modal{width:520px;max-width:98vw;padding:24px}
.edit-title{font-size:20px;font-weight:800;color:#1e293b;margin-bottom:4px;text-align:center}
.edit-base-price{font-size:14px;color:#64748b;text-align:center;margin-bottom:16px}
.edit-section{margin-bottom:16px}
.edit-section h4{font-size:14px;color:#64748b;margin-bottom:8px;font-weight:700}
.edit-toggles{display:flex;flex-wrap:wrap;gap:8px}
.edit-toggle{padding:10px 16px;border-radius:10px;font-size:14px;font-weight:600;
  background:#f1f5f9;color:#475569;min-height:44px;transition:.15s}
.edit-toggle.active{background:#3b82f6;color:#fff}
.edit-toggle.active.remove{background:#ef4444;color:#fff}
.edit-toggle.active.extra{background:#f59e0b;color:#fff}
.edit-toggle:active{transform:scale(.95)}
.edit-notes{margin-top:8px}
.edit-notes input{width:100%;height:48px;border:2px solid #e2e8f0;border-radius:10px;padding:0 14px;
  font-size:16px;background:#fff}
.edit-notes input:focus{border-color:#3b82f6}
.edit-notes input[readonly]{cursor:pointer;background:#f8fafc}
.edit-price{font-size:24px;font-weight:800;text-align:center;margin:14px 0;color:#1e293b}
.edit-actions{display:flex;gap:10px}
.edit-actions button{flex:1;padding:14px;border-radius:12px;font-size:16px;font-weight:700;min-height:54px}
.btn-edit-save{background:#16a34a;color:#fff}
.btn-edit-cancel{background:#64748b;color:#fff}

/* == ADMIN MODAL == */
.admin-modal .modal{width:700px;max-width:98vw;padding:24px}
.admin-title{font-size:22px;font-weight:800;margin-bottom:16px;color:#1e293b}
.admin-section{margin-bottom:20px}
.admin-section h3{font-size:16px;color:#3b82f6;margin-bottom:10px;font-weight:700}
.admin-form{display:flex;flex-wrap:wrap;gap:10px;align-items:flex-end}
.admin-field{display:flex;flex-direction:column;gap:4px}
.admin-field label{font-size:13px;color:#64748b;font-weight:600}
.admin-field input,.admin-field select{height:48px;border:2px solid #e2e8f0;border-radius:10px;
  padding:0 12px;font-size:16px;min-width:140px;background:#fff}
.admin-field input:focus{border-color:#3b82f6}
.admin-field input[readonly]{background:#f1f5f9;cursor:pointer}
.btn-admin-add{height:48px;padding:0 20px;background:#3b82f6;color:#fff;border-radius:10px;
  font-size:15px;font-weight:700}
.btn-admin-add:active{background:#2563eb}
.admin-list{max-height:340px;overflow-y:auto;-webkit-overflow-scrolling:touch}
.admin-list table{width:100%;border-collapse:collapse}
.admin-list th{background:#f1f5f9;padding:10px 8px;font-size:13px;color:#64748b;text-align:left;
  position:sticky;top:0}
.admin-list td{padding:8px;border-bottom:1px solid #f1f5f9;font-size:14px;vertical-align:middle}
.admin-list .al-price{font-weight:700;cursor:pointer;color:#16a34a;background:#f0fdf4;
  border-radius:6px;padding:4px 8px;display:inline-block;min-width:60px;text-align:right}
.admin-list .al-price:active{background:#dcfce7}
.admin-list .al-del{width:40px;height:40px;border-radius:8px;background:#fef2f2;color:#ef4444;font-size:16px}
.admin-list .al-del:active{background:#ef4444;color:#fff}
.btn-admin-close{width:100%;padding:14px;background:#64748b;color:#fff;border-radius:12px;
  font-size:16px;font-weight:700;margin-top:10px}
.btn-admin-close:active{background:#475569}

/* == SALES MODAL == */
.ventas-modal .modal{width:650px;max-width:98vw;padding:24px}
.ventas-title{font-size:22px;font-weight:800;margin-bottom:6px}
.ventas-summary{display:flex;gap:16px;margin-bottom:14px}
.ventas-summary .vs-box{flex:1;background:#f1f5f9;border-radius:12px;padding:14px;text-align:center}
.vs-box .vs-val{font-size:24px;font-weight:800;color:#1e293b}
.vs-box .vs-lbl{font-size:13px;color:#64748b}
.ventas-list{max-height:360px;overflow-y:auto;-webkit-overflow-scrolling:touch}
.ventas-list table{width:100%;border-collapse:collapse}
.ventas-list th{background:#f1f5f9;padding:10px 8px;font-size:13px;color:#64748b;text-align:left;position:sticky;top:0}
.ventas-list td{padding:8px;border-bottom:1px solid #f1f5f9;font-size:14px}
.btn-ventas-close{width:100%;padding:14px;background:#64748b;color:#fff;border-radius:12px;
  font-size:16px;font-weight:700;margin-top:12px}

/* == RECEIPT MODAL == */
.receipt-modal .modal{width:360px;padding:0;border-radius:12px;overflow:hidden}
#receipt-content{font-family:'Arial',Helvetica,sans-serif;font-size:13px;padding:0 15px 100px 25px;background:#fff;color:#000;line-height:1.5}
#receipt-content .rc-center{text-align:center}
#receipt-content .rc-bold{font-weight:700}
#receipt-content .rc-line{border-bottom:1px dashed #000;margin:8px 0}
#receipt-content .rc-row{display:flex;justify-content:space-between}
#receipt-content .rc-total-row{display:flex;justify-content:space-between;font-weight:700;font-size:16px;margin-top:4px}
#receipt-content .rc-mods{color:#666;font-size:11px;margin-left:10px}
.receipt-actions{display:flex;gap:8px;padding:12px 16px;background:#f1f5f9}
.receipt-actions button{flex:1;padding:12px;border-radius:10px;font-size:15px;font-weight:700;min-height:48px}
.btn-receipt-print{background:#3b82f6;color:#fff}
.btn-receipt-close{background:#64748b;color:#fff}

/* == VIRTUAL KEYBOARD == */
.vkb-overlay{position:fixed;inset:0;background:rgba(0,0,0,.45);display:none;
  justify-content:center;align-items:flex-end;z-index:200;padding-bottom:20px}
.vkb-overlay.open{display:flex}
.vkb{background:#e8eaed;border-radius:16px 16px 0 0;padding:12px;width:100%;max-width:700px}
.vkb-display{background:#fff;border-radius:10px;padding:12px 16px;font-size:22px;
  min-height:50px;margin-bottom:10px;color:#1e293b;word-break:break-all;text-align:left}
.vkb-row{display:flex;gap:6px;justify-content:center;margin-bottom:6px}
.vkb-row button{height:52px;min-width:42px;border-radius:8px;font-size:18px;font-weight:600;
  background:#fff;color:#1e293b;flex:0 0 auto;padding:0 12px;transition:.1s}
.vkb-row button:active{background:#c8ccd0}
.vkb-row .vk-space{flex:1;max-width:280px}
.vkb-row .vk-back{background:#fef2f2;color:#ef4444;min-width:70px}
.vkb-row .vk-enter{background:#3b82f6;color:#fff;min-width:90px}
.vkb-row .vk-shift{background:#e2e8f0;min-width:70px}
.vkb-row .vk-cancel{background:#ef4444;color:#fff;min-width:90px}

/* == TOAST == */
#toast{position:fixed;top:70px;left:50%;transform:translateX(-50%);background:#1e293b;color:#fff;
  padding:14px 28px;border-radius:12px;font-size:16px;font-weight:600;z-index:300;
  opacity:0;transition:opacity .3s;pointer-events:none}
#toast.show{opacity:1}

/* == PRINT STYLES == */
@media print{
  @page{margin:0;size:80mm auto}
  body *{visibility:hidden}
  .receipt-modal,.receipt-modal *{visibility:visible}
  .receipt-modal .modal{position:absolute;left:0;top:0;width:80mm;box-shadow:none;border-radius:0}
  .receipt-actions{display:none!important}
}
</style>
</head>
<body>
<div id="app">
  <!-- TOP BAR -->
  <div id="topbar">
    <div class="title">🐔 Pollería Las Portadas</div>
    <div class="clock" id="clock">00:00:00</div>
    <button onclick="openVentas()">📊 Ventas</button>
    <button onclick="openAdmin()">⚙️ Gestión</button>
  </div>
  <div id="left">
    <div id="cat-tabs"></div>
    <div id="subcat-tabs"></div>
    <div id="prod-grid"></div>
  </div>
  <div id="right">
    <div id="order-header"><span>🛒 Pedido Actual</span></div>
    <div id="order-list"><div id="order-empty">Toca un producto para empezar</div></div>
    <div id="order-footer">
      <div id="order-total">TOTAL: 0,00 €</div>
      <div id="order-actions">
        <button id="btn-limpiar" onclick="clearOrder()">🗑️ Limpiar</button>
        <button id="btn-cobrar" onclick="openPay()" disabled>💶 Cobrar</button>
      </div>
    </div>
  </div>
</div>

<!-- NUMPAD MODAL -->
<div class="modal-overlay numpad-modal" id="numpad-overlay">
  <div class="modal">
    <div class="numpad-title" id="numpad-title">Introduce valor</div>
    <div class="numpad-display" id="numpad-display">0</div>
    <div class="numpad-grid">
      <button onclick="npPress('7')">7</button><button onclick="npPress('8')">8</button><button onclick="npPress('9')">9</button>
      <button onclick="npPress('4')">4</button><button onclick="npPress('5')">5</button><button onclick="npPress('6')">6</button>
      <button onclick="npPress('1')">1</button><button onclick="npPress('2')">2</button><button onclick="npPress('3')">3</button>
      <button class="np-clear" onclick="npClear()">C</button><button onclick="npPress('0')">0</button><button onclick="npPress('.')">.</button>
      <button class="np-back" onclick="npBack()">⌫</button><button class="np-confirm" style="grid-column:span 2" onclick="npConfirm()">✔ Aceptar</button>
    </div>
  </div>
</div>

<!-- PAYMENT MODAL -->
<div class="modal-overlay pay-modal" id="pay-overlay">
  <div class="modal">
    <div class="pay-total-label">Total a cobrar</div>
    <div class="pay-total" id="pay-total">0,00 €</div>
    <div class="pay-tabs">
      <button class="pay-tab tab-efectivo active" onclick="setPayTab('efectivo')">💵 Efectivo</button>
      <button class="pay-tab tab-tarjeta" onclick="setPayTab('tarjeta')">💳 Tarjeta</button>
      <button class="pay-tab tab-mixto" onclick="setPayTab('mixto')">💵+💳 Mixto</button>
    </div>
    <!-- EFECTIVO -->
    <div class="pay-section active" id="pay-sec-efectivo">
      <div class="pay-field-label">💵 Efectivo entregado</div>
      <div class="pay-cash-display" id="pay-cash-ef">0</div>
      <div class="pay-numpad" id="pay-np-ef">
        <button onclick="payNp('7','ef')">7</button><button onclick="payNp('8','ef')">8</button><button onclick="payNp('9','ef')">9</button>
        <button onclick="payNp('4','ef')">4</button><button onclick="payNp('5','ef')">5</button><button onclick="payNp('6','ef')">6</button>
        <button onclick="payNp('1','ef')">1</button><button onclick="payNp('2','ef')">2</button><button onclick="payNp('3','ef')">3</button>
        <button class="np-clear" onclick="payNpClear('ef')">C</button><button onclick="payNp('0','ef')">0</button><button onclick="payNp('.','ef')">.</button>
        <button class="np-back" onclick="payNpBack('ef')" style="grid-column:span 3">⌫ Borrar</button>
      </div>
      <div class="pay-change negative" id="pay-change-ef">Cambio: 0,00 €</div>
    </div>
    <!-- TARJETA -->
    <div class="pay-section" id="pay-sec-tarjeta">
      <div class="pay-card-info">
        <span class="big-icon">💳</span>
        Cobro con tarjeta por el total<br><strong id="pay-card-total">0,00 €</strong>
      </div>
    </div>
    <!-- MIXTO -->
    <div class="pay-section" id="pay-sec-mixto">
      <div class="pay-field-label">💵 Parte en efectivo</div>
      <div class="pay-cash-display" id="pay-cash-mx">0</div>
      <div class="pay-numpad" id="pay-np-mx">
        <button onclick="payNp('7','mx')">7</button><button onclick="payNp('8','mx')">8</button><button onclick="payNp('9','mx')">9</button>
        <button onclick="payNp('4','mx')">4</button><button onclick="payNp('5','mx')">5</button><button onclick="payNp('6','mx')">6</button>
        <button onclick="payNp('1','mx')">1</button><button onclick="payNp('2','mx')">2</button><button onclick="payNp('3','mx')">3</button>
        <button class="np-clear" onclick="payNpClear('mx')">C</button><button onclick="payNp('0','mx')">0</button><button onclick="payNp('.','mx')">.</button>
        <button class="np-back" onclick="payNpBack('mx')" style="grid-column:span 3">⌫ Borrar</button>
      </div>
      <div class="pay-field-label">💳 Parte con tarjeta (automático)</div>
      <div class="pay-card-display" id="pay-card-mx">0,00 €</div>
    </div>
    <div class="pay-actions">
      <button id="btn-pay-cancel" onclick="closePay()">✖ Cancelar</button>
      <button id="btn-pay-confirm" disabled onclick="confirmPay()">✔ Confirmar Pago</button>
    </div>
  </div>
</div>

<!-- EDIT ITEM MODAL -->
<div class="modal-overlay edit-modal" id="edit-overlay">
  <div class="modal">
    <div class="edit-title" id="edit-title">Editar producto</div>
    <div class="edit-base-price" id="edit-base-price">Precio base: 0,00 €</div>
    <div class="edit-section">
      <h4>🚫 Quitar ingrediente (gratis)</h4>
      <div class="edit-toggles" id="edit-removals"></div>
    </div>
    <div class="edit-section">
      <h4>➕ Extras (+0,80 € cada uno)</h4>
      <div class="edit-toggles" id="edit-extras"></div>
    </div>
    <div class="edit-section edit-notes">
      <h4>📝 Notas</h4>
      <input id="edit-notes-input" readonly placeholder="Toca para escribir nota" onclick="openVKB('edit-notes-input')">
    </div>
    <div class="edit-price" id="edit-final-price">Precio: 0,00 €</div>
    <div class="edit-actions">
      <button class="btn-edit-cancel" onclick="closeEdit()">Cancelar</button>
      <button class="btn-edit-save" onclick="saveEdit()">✔ Guardar</button>
    </div>
  </div>
</div>

<!-- ADMIN MODAL -->
<div class="modal-overlay admin-modal" id="admin-overlay">
  <div class="modal">
    <div class="admin-title">⚙️ Gestión de Productos</div>
    <div class="admin-section">
      <h3>➕ Añadir nuevo plato</h3>
      <div class="admin-form">
        <div class="admin-field"><label>Nombre</label>
          <input id="adm-name" readonly placeholder="Toca para escribir" onclick="openVKB('adm-name')">
        </div>
        <div class="admin-field"><label>Precio (€)</label>
          <input id="adm-price" readonly placeholder="0.00" onclick="openNumpad('Precio del plato',function(v){document.getElementById('adm-price').value=v})">
        </div>
        <div class="admin-field"><label>Categoría</label>
          <select id="adm-cat">
            <option>Aliños</option>
            <option>Bebidas</option>
            <option>Bocatas</option>
            <option>Bocatas / Especiales</option>
            <option>Fritos</option>
            <option>Fritos / Croquetas caseras</option>
            <option>Hamburguesas</option>
            <option>Ingredientes extra</option>
            <option>Otros platos</option>
            <option>Otros platos / Extra</option>
            <option>Patatas</option>
            <option>Perritos</option>
            <option>Pollos</option>
            <option>Salsas</option>
            <option>Sándwiches</option>
          </select>
        </div>
        <button class="btn-admin-add" onclick="adminAdd()">Añadir</button>
      </div>
    </div>
    <div class="admin-section">
      <h3>📋 Productos actuales</h3>
      <div class="admin-list" id="admin-list"></div>
    </div>
    <button class="btn-admin-close" onclick="closeAdmin()">Cerrar</button>
  </div>
</div>

<!-- SALES MODAL -->
<div class="modal-overlay ventas-modal" id="ventas-overlay">
  <div class="modal">
    <div class="ventas-title">📊 Ventas de Hoy</div>
    <div class="ventas-summary" id="ventas-summary"></div>
    <div class="ventas-list" id="ventas-list"></div>
    <button class="btn-ventas-close" onclick="closeVentas()">Cerrar</button>
  </div>
</div>

<!-- RECEIPT MODAL -->
<div class="modal-overlay receipt-modal" id="receipt-overlay">
  <div class="modal">
    <div id="receipt-content"></div>
    <div class="receipt-actions">
      <label style="display:flex; align-items:center; gap:8px; font-weight:bold; cursor:pointer;">
        <input type="checkbox" id="chk-duplicar" style="width:20px; height:20px;"> Duplicar ticket
      </label>
      <button class="btn-receipt-close" onclick="closeReceipt()">Cerrar</button>
      <button class="btn-receipt-print" onclick="window.print()">🖨️ Imprimir</button>
    </div>
  </div>
</div>

<!-- VIRTUAL KEYBOARD -->
<div class="vkb-overlay" id="vkb-overlay">
  <div class="vkb">
    <div class="vkb-display" id="vkb-display"></div>
    <div id="vkb-keys"></div>
  </div>
</div>

<!-- TOAST -->
<div id="toast"></div>

<script>
/* === STATE === */
let productos = [];
// order items: {id, nombre, precioBase, qty, removals:[], extras:[], notes:"", precioUnit (computed)}
let order = [];
let npCallback = null;
let npValue = "0";
let vkbTarget = null;
let vkbShift = false;
let payMode = "efectivo";
let payVals = {ef:"0", mx:"0"};
let editIdx = -1;

const EXTRA_PRICE = 0.80;
const REMOVALS = ["Sin lechuga","Sin tomate","Sin cebolla","Sin salsa","Sin queso","Sin pan"];
const EXTRAS = ["Extra bacon","Extra queso","Extra huevo","Extra salsa"];

/* === CLOCK === */
function updateClock(){
  const d=new Date();
  document.getElementById("clock").textContent =
    [d.getHours(),d.getMinutes(),d.getSeconds()].map(n=>String(n).padStart(2,"0")).join(":");
}
setInterval(updateClock,1000); updateClock();

/* === LOAD PRODUCTS === */
async function loadProducts(){
  const r=await fetch("/api/productos"); productos=await r.json(); renderCats(); renderGrid();
}

let activeCat="Todos";
let activeSubCat="Todos"; // Nuevo estado para la subcategoría

// Extraer solo la categoría Padre (lo que hay antes del '/')
function getParentCats(){ 
  return [...new Set(productos.map(p => p.categoria.split('/')[0].trim()))]; 
}

// Extraer las categorías completas que pertenecen a un Padre específico
function getSubCats(parent){
  return [...new Set(productos.filter(p => p.categoria.split('/')[0].trim() === parent).map(p => p.categoria))];
}

function renderCats(){
  const parents=["Todos",...getParentCats()];
  document.getElementById("cat-tabs").innerHTML=parents.map(c=>
    `<button class="cat-tab ${c===activeCat?"active":""}" onclick="setCat('${c}')">${c}</button>`
  ).join("");
  renderSubCats(); // Llamar a renderizar subcategorías
}

function renderSubCats(){
  const el = document.getElementById("subcat-tabs");
  if(activeCat === "Todos"){
    el.innerHTML = ""; // Si estamos en "Todos", no hay subpestañas
    return;
  }
  
  const subs = getSubCats(activeCat);
  if(subs.length <= 1){
    el.innerHTML = ""; // Si no hay subcategorías (solo hay 1 tipo general), ocultamos la barra
    return;
  }

  // Pintar el botón de "Todo" dentro de la categoría y luego un botón por subcategoría
  let html = `<button class="subcat-tab ${activeSubCat==="Todos"?"active":""}" onclick="setSubCat('Todos')">Todo ${activeCat}</button>`;
  
  subs.forEach(s => {
    // Si tiene '/', el nombre del botón es la parte derecha. Si no, le llamamos "Generales"
    const label = s.includes('/') ? s.split('/')[1].trim() : "Generales";
    html += `<button class="subcat-tab ${activeSubCat===s?"active":""}" onclick="setSubCat('${s}')">${label}</button>`;
  });
  
  el.innerHTML = html;
}

function setCat(c){ 
  activeCat = c; 
  activeSubCat = "Todos"; // Al cambiar de padre, reseteamos la subcategoría
  renderCats(); 
  renderGrid(); 
}

function setSubCat(sc){
  activeSubCat = sc;
  renderSubCats(); // Solo repintamos las subpestañas para actualizar la clase activa
  renderGrid();
}

function renderGrid(){
  let list = productos;
  
  if(activeCat !== "Todos"){
    if(activeSubCat === "Todos"){
      // Mostrar todos los productos del padre seleccionado (incluye subcategorías)
      list = productos.filter(p => p.categoria.split('/')[0].trim() === activeCat);
    } else {
      // Mostrar solo los productos de la subcategoría exacta elegida
      list = productos.filter(p => p.categoria === activeSubCat);
    }
  }
  
  document.getElementById("prod-grid").innerHTML=list.map(p=>
    `<button class="prod-btn" onclick="addToOrder(${p.id})">
      <span class="pname">${esc(p.nombre)}</span>
      <span class="pprice">${p.precio.toFixed(2)} €</span>
    </button>`
  ).join("");
}

function esc(s){return s.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");}

/* === ORDER === */
function addToOrder(id){
  const p=productos.find(x=>x.id===id); if(!p) return;
  // Each addition is a new line so user can customize independently
  order.push({id:p.id,nombre:p.nombre,precioBase:p.precio,qty:1,removals:[],extras:[],notes:""});
  renderOrder();
}
function getItemPrice(it){ return it.precioBase + it.extras.length * EXTRA_PRICE; }
function getModsText(it){
  let parts=[];
  it.removals.forEach(r=>parts.push(r));
  it.extras.forEach(e=>parts.push(e+" +0,80€"));
  if(it.notes) parts.push("📝 "+it.notes);
  return parts.join(" · ");
}
function renderOrder(){
  const el=document.getElementById("order-list");
  if(!order.length){ el.innerHTML='<div id="order-empty">Toca un producto para empezar</div>'; }
  else{
    el.innerHTML=order.map((it,i)=>{
      const up=getItemPrice(it);
      const mods=getModsText(it);
      return `<div class="order-item">
        <div class="oi-info" onclick="openEdit(${i})">
          <span class="oi-name">✏️ ${esc(it.nombre)}</span>
          ${mods?'<span class="oi-mods">'+esc(mods)+'</span>':""}
        </div>
        <span class="oi-qty">
          <button class="oi-minus" onclick="oqty(${i},-1)">−</button>
          <span class="oi-qval">${it.qty}</span>
          <button class="oi-plus" onclick="oqty(${i},1)">+</button>
        </span>
        <span class="oi-sub">${(up*it.qty).toFixed(2)} €</span>
        <button class="oi-del" onclick="odel(${i})">✕</button>
      </div>`;
    }).join("");
  }
  const total=getTotal();
  document.getElementById("order-total").textContent="TOTAL: "+total.toFixed(2)+" €";
  document.getElementById("btn-cobrar").disabled=!order.length;
}
function oqty(i,d){ order[i].qty+=d; if(order[i].qty<1) order.splice(i,1); renderOrder(); }
function odel(i){ order.splice(i,1); renderOrder(); }
function clearOrder(){ order=[]; renderOrder(); }
function getTotal(){ return order.reduce((s,x)=>s+getItemPrice(x)*x.qty,0); }

/* === EDIT ITEM === */
let editRemovals=[], editExtras=[], editNotes="";
function openEdit(i){
  editIdx=i;
  const it=order[i];
  editRemovals=[...it.removals]; editExtras=[...it.extras]; editNotes=it.notes;
  document.getElementById("edit-title").textContent="✏️ "+it.nombre;
  document.getElementById("edit-base-price").textContent="Precio base: "+it.precioBase.toFixed(2)+" €";
  document.getElementById("edit-notes-input").value=editNotes;
  renderEditToggles();
  updateEditPrice();
  document.getElementById("edit-overlay").classList.add("open");
}
function renderEditToggles(){
  document.getElementById("edit-removals").innerHTML=REMOVALS.map(r=>{
    const active=editRemovals.includes(r);
    return `<button class="edit-toggle remove ${active?"active":""}" onclick="toggleRemoval('${r}')">${r}</button>`;
  }).join("");
  document.getElementById("edit-extras").innerHTML=EXTRAS.map(e=>{
    const active=editExtras.includes(e);
    return `<button class="edit-toggle extra ${active?"active":""}" onclick="toggleExtra('${e}')">${e}</button>`;
  }).join("");
}
function toggleRemoval(r){
  const idx=editRemovals.indexOf(r);
  if(idx>=0) editRemovals.splice(idx,1); else editRemovals.push(r);
  renderEditToggles(); updateEditPrice();
}
function toggleExtra(e){
  const idx=editExtras.indexOf(e);
  if(idx>=0) editExtras.splice(idx,1); else editExtras.push(e);
  renderEditToggles(); updateEditPrice();
}
function updateEditPrice(){
  const it=order[editIdx];
  const p=it.precioBase+editExtras.length*EXTRA_PRICE;
  document.getElementById("edit-final-price").textContent="Precio: "+p.toFixed(2)+" €";
}
function saveEdit(){
  order[editIdx].removals=[...editRemovals];
  order[editIdx].extras=[...editExtras];
  order[editIdx].notes=document.getElementById("edit-notes-input").value.trim();
  closeEdit();
  renderOrder();
}
function closeEdit(){ document.getElementById("edit-overlay").classList.remove("open"); }

/* === NUMPAD MODAL === */
function openNumpad(title,cb){
  npValue="0"; npCallback=cb;
  document.getElementById("numpad-title").textContent=title;
  document.getElementById("numpad-display").textContent="0";
  document.getElementById("numpad-overlay").classList.add("open");
}
function npPress(c){
  if(npValue==="0"&&c!==".") npValue=c;
  else{if(c==="."&&npValue.includes(".")) return; npValue+=c;}
  document.getElementById("numpad-display").textContent=npValue;
}
function npBack(){ npValue=npValue.slice(0,-1)||"0"; document.getElementById("numpad-display").textContent=npValue; }
function npClear(){ npValue="0"; document.getElementById("numpad-display").textContent="0"; }
function npConfirm(){
  document.getElementById("numpad-overlay").classList.remove("open");
  const v=parseFloat(npValue)||0;
  if(npCallback) npCallback(v.toFixed(2));
}

/* === PAYMENT MODAL === */
function openPay(){
  payVals={ef:"0",mx:"0"}; payMode="efectivo";
  document.getElementById("pay-total").textContent=getTotal().toFixed(2)+" €";
  document.getElementById("pay-card-total").textContent=getTotal().toFixed(2)+" €";
  document.getElementById("pay-cash-ef").textContent="0";
  document.getElementById("pay-cash-mx").textContent="0";
  document.getElementById("pay-card-mx").textContent=getTotal().toFixed(2)+" €";
  setPayTab("efectivo");
  document.getElementById("pay-overlay").classList.add("open");
}
function closePay(){ document.getElementById("pay-overlay").classList.remove("open"); }
function setPayTab(mode){
  payMode=mode;
  document.querySelectorAll(".pay-tab").forEach(t=>t.classList.remove("active"));
  document.querySelector(".tab-"+mode).classList.add("active");
  document.querySelectorAll(".pay-section").forEach(s=>s.classList.remove("active"));
  document.getElementById("pay-sec-"+mode).classList.add("active");
  updatePayState();
}
function payNp(c,t){
  if(payVals[t]==="0"&&c!==".") payVals[t]=c;
  else{if(c==="."&&payVals[t].includes(".")) return; payVals[t]+=c;}
  document.getElementById("pay-cash-"+t).textContent=payVals[t];
  updatePayState();
}
function payNpBack(t){ payVals[t]=payVals[t].slice(0,-1)||"0"; document.getElementById("pay-cash-"+t).textContent=payVals[t]; updatePayState(); }
function payNpClear(t){ payVals[t]="0"; document.getElementById("pay-cash-"+t).textContent="0"; updatePayState(); }
function updatePayState(){
  const total=getTotal();
  let canPay=false;
  if(payMode==="efectivo"){
    const cash=parseFloat(payVals.ef)||0;
    const change=cash-total;
    const el=document.getElementById("pay-change-ef");
    el.textContent="Cambio: "+(change>=0?"+":"")+change.toFixed(2)+" €";
    el.className="pay-change "+(change>=0?"positive":"negative");
    canPay=change>=0;
  } else if(payMode==="tarjeta"){
    canPay=true;
  } else {
    const cashPart=Math.min(parseFloat(payVals.mx)||0, total);
    const cardPart=Math.max(total-cashPart,0);
    document.getElementById("pay-card-mx").textContent=cardPart.toFixed(2)+" €";
    canPay=(cashPart+cardPart)>=total-0.001;
  }
  document.getElementById("btn-pay-confirm").disabled=!canPay;
}

async function confirmPay(){
  const total=getTotal();
  let pagado_efectivo=0, pagado_tarjeta=0, cambio=0, metodo=payMode;
  if(payMode==="efectivo"){
    pagado_efectivo=parseFloat(payVals.ef)||0;
    cambio=pagado_efectivo-total;
    pagado_tarjeta=0;
  } else if(payMode==="tarjeta"){
    pagado_tarjeta=total;
    pagado_efectivo=0; cambio=0;
  } else {
    pagado_efectivo=Math.min(parseFloat(payVals.mx)||0,total);
    pagado_tarjeta=Math.max(total-pagado_efectivo,0);
    cambio=0;
  }
  const items=order.map(x=>({nombre:x.nombre,qty:x.qty,precioBase:x.precioBase,
    precioUnit:getItemPrice(x),removals:x.removals,extras:x.extras,notes:x.notes}));
  const body={items,total:total.toFixed(2),
    pagado_efectivo:pagado_efectivo.toFixed(2),pagado_tarjeta:pagado_tarjeta.toFixed(2),
    cambio:cambio.toFixed(2),metodo_pago:metodo};
  const res=await fetch("/api/venta",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const rd=await res.json();
  closePay();
  showReceipt(items,total,pagado_efectivo,pagado_tarjeta,cambio,metodo,rd.timestamp);
  clearOrder();
  toast("✅ Venta registrada correctamente");
}

/* === RECEIPT === */
function showReceipt(items, total, efect, tarj, cambio, metodo, ts) {
    const duplicar = document.getElementById('chk-duplicar').checked;
    
    // Función interna para generar el diseño de un solo ticket
    const generarTicketHTML = (subtitulo = "") => {
        const totalNum = parseFloat(total);
        const baseImponible = totalNum / 1.10;
        const cuotaIva = totalNum - baseImponible;

        let h = '<div class="ticket-individual" style="padding-bottom: 20px;">';
        h += '<div class="rc-center rc-bold" style="font-size:16px">POLLERÍA LAS PORTADAS</div>';
        if(subtitulo) h += `<div class="rc-center rc-bold">*** ${subtitulo} ***</div>`;
        h += '<div class="rc-center">Av. las Portadas, 2</div>';
        h += '<div class="rc-center">41700 Dos Hermanas, Sevilla</div>';
        h += '<div class="rc-center">Tel: 623700918</div>';
        h += '<div class="rc-center">DNI: 48964063F</div>';
        h += '<div class="rc-line"></div>';
        h += `<div class="rc-center">${esc(ts)}</div>`;
        h += '<div class="rc-line"></div>';
        
        items.forEach(it => {
            const lineTotal = (it.precioUnit * it.qty).toFixed(2);
            h += `<div class="rc-row"><span>${it.qty}x ${esc(it.nombre)}</span><span>${lineTotal} €</span></div>`;
            let mods = [];
            it.removals.forEach(r => mods.push("  " + r));
            it.extras.forEach(e => mods.push("  " + e + " +0,80€"));
            if (it.notes) mods.push("  📝 " + it.notes);
            if (mods.length) h += '<div class="rc-mods">' + mods.map(m => esc(m)).join("<br>") + '</div>';
        });

        h += '<div class="rc-line"></div>';
        h += `<div class="rc-total-row"><span>TOTAL (IVA incl.)</span><span>${totalNum.toFixed(2)} €</span></div>`;
        
        // DESGLOSE DE IVA PARA HACIENDA
        h += '<div style="margin-top: 5px; font-size: 11px;">';
        h += `<div class="rc-row"><span>Base Imponible (10%):</span><span>${baseImponible.toFixed(2)} €</span></div>`;
        h += `<div class="rc-row"><span>Cuota IVA:</span><span>${cuotaIva.toFixed(2)} €</span></div>`;
        h += '</div>';

        h += '<div class="rc-line"></div>';
        if (metodo === "efectivo") {
            h += `<div class="rc-row"><span>Efectivo</span><span>${parseFloat(efect).toFixed(2)} €</span></div>`;
            h += `<div class="rc-row"><span>Cambio</span><span>${parseFloat(cambio).toFixed(2)} €</span></div>`;
        } else if (metodo === "tarjeta") {
            h += `<div class="rc-row"><span>Tarjeta</span><span>${parseFloat(tarj).toFixed(2)} €</span></div>`;
        } else {
            h += `<div class="rc-row"><span>Efectivo</span><span>${parseFloat(efect).toFixed(2)} €</span></div>`;
            h += `<div class="rc-row"><span>Tarjeta</span><span>${parseFloat(tarj).toFixed(2)} €</span></div>`;
        }
        h += '<div class="rc-line"></div>';
        h += '<div class="rc-center rc-bold" style="margin-top:8px">¡Gracias por su visita!</div>';
        h += '<div style="height: 20px; color: white;">.</div>';       
        h += '</div>';
        return h;
    };

    // Generamos el contenido (uno o dos tickets)
    let finalHTML = generarTicketHTML(); // Ticket original
    if (duplicar) {
        finalHTML += '<div style="border-top: 2px dashed #000; margin: 40px 0; position: relative;">' + 
                     '<span style="position:absolute; top:-12px; left:50%; transform:translateX(-50%); background:#fff; padding:0 10px; font-size:10px;">TIJERAS / CORTE</span></div>';
        finalHTML += generarTicketHTML("COPIA PARA CAJA");
    }

    document.getElementById("receipt-content").innerHTML = finalHTML;
    document.getElementById("receipt-overlay").classList.add("open");
}
function closeReceipt(){ document.getElementById("receipt-overlay").classList.remove("open"); }

/* === ADMIN === */
function openAdmin(){ renderAdminList(); document.getElementById("admin-overlay").classList.add("open"); }
function closeAdmin(){ document.getElementById("admin-overlay").classList.remove("open"); }
function renderAdminList(){
  let html='<table><tr><th>ID</th><th>Nombre</th><th>Categoría</th><th>Precio</th><th></th></tr>';
  productos.forEach(p=>{
    html+=`<tr>
      <td>${p.id}</td><td>${esc(p.nombre)}</td><td>${esc(p.categoria)}</td>
      <td><span class="al-price" onclick="editPrice(${p.id})">${p.precio.toFixed(2)} €</span></td>
      <td><button class="al-del" onclick="delProd(${p.id})">🗑</button></td>
    </tr>`;
  });
  html+="</table>";
  document.getElementById("admin-list").innerHTML=html;
}
function editPrice(id){
  openNumpad("Nuevo precio",async function(v){
    await fetch("/api/productos/"+id,{method:"PUT",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({precio:parseFloat(v)})});
    await loadProducts(); renderAdminList(); toast("💲 Precio actualizado");
  });
}
async function delProd(id){
  await fetch("/api/productos/"+id,{method:"DELETE"});
  await loadProducts(); renderAdminList(); toast("🗑️ Producto eliminado");
}
async function adminAdd(){
  const nombre=document.getElementById("adm-name").value.trim();
  const precio=parseFloat(document.getElementById("adm-price").value)||0;
  const cat=document.getElementById("adm-cat").value;
  if(!nombre){toast("⚠️ Escribe un nombre"); return;}
  if(precio<=0){toast("⚠️ Introduce un precio válido"); return;}
  await fetch("/api/productos",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({nombre,precio,categoria:cat})});
  document.getElementById("adm-name").value="";
  document.getElementById("adm-price").value="";
  await loadProducts(); renderAdminList(); toast("✅ Producto añadido");
}

/* === VENTAS === */
async function openVentas(){
  const r=await fetch("/api/ventas"); const ventas=await r.json();
  const nv=ventas.length;
  const totalDay=ventas.reduce((s,v)=>s+parseFloat(v.total),0);
  const totalEf=ventas.reduce((s,v)=>s+parseFloat(v.pagado_efectivo||0),0);
  const totalTj=ventas.reduce((s,v)=>s+parseFloat(v.pagado_tarjeta||0),0);
  document.getElementById("ventas-summary").innerHTML=
    `<div class="vs-box"><div class="vs-val">${nv}</div><div class="vs-lbl">Ventas</div></div>
     <div class="vs-box"><div class="vs-val">${totalDay.toFixed(2)} €</div><div class="vs-lbl">Facturado</div></div>
     <div class="vs-box"><div class="vs-val">${totalEf.toFixed(2)} €</div><div class="vs-lbl">Efectivo</div></div>
     <div class="vs-box"><div class="vs-val">${totalTj.toFixed(2)} €</div><div class="vs-lbl">Tarjeta</div></div>`;
  let html='<table><tr><th>Hora</th><th>Items</th><th>Total</th><th>Método</th></tr>';
  ventas.forEach(v=>{
    const hora=(v.timestamp||"").split(" ")[1]||v.timestamp;
    const items=(v.items||[]).map(x=>x.qty+"x "+x.nombre).join(", ");
    const met=v.metodo_pago||"efectivo";
    html+=`<tr><td>${esc(hora)}</td><td>${esc(items)}</td><td>${parseFloat(v.total).toFixed(2)} €</td>
      <td>${esc(met)}</td></tr>`;
  });
  html+="</table>";
  document.getElementById("ventas-list").innerHTML=html;
  document.getElementById("ventas-overlay").classList.add("open");
}
function closeVentas(){ document.getElementById("ventas-overlay").classList.remove("open"); }

/* === VIRTUAL KEYBOARD === */
const ROWS_LOWER=[
  ["1","2","3","4","5","6","7","8","9","0"],
  ["q","w","e","r","t","y","u","i","o","p"],
  ["SHIFT","a","s","d","f","g","h","j","k","l","ñ"],
  ["z","x","c","v","b","n","m","BACK"],
  ["CANCEL","SPACE","ENTER"]
];
const ROWS_UPPER=[
  ["1","2","3","4","5","6","7","8","9","0"],
  ["Q","W","E","R","T","Y","U","I","O","P"],
  ["SHIFT","A","S","D","F","G","H","J","K","L","Ñ"],
  ["Z","X","C","V","B","N","M","BACK"],
  ["CANCEL","SPACE","ENTER"]
];
function openVKB(targetId){
  vkbTarget=document.getElementById(targetId); vkbShift=false; renderVKB();
  document.getElementById("vkb-display").textContent=vkbTarget.value;
  document.getElementById("vkb-overlay").classList.add("open");
}
function closeVKB(){ document.getElementById("vkb-overlay").classList.remove("open"); }
function renderVKB(){
  const rows=vkbShift?ROWS_UPPER:ROWS_LOWER;
  document.getElementById("vkb-keys").innerHTML=rows.map(row=>
    '<div class="vkb-row">'+row.map(k=>{
      if(k==="SPACE") return '<button class="vk-space" onclick="vkType(\' \')">Espacio</button>';
      if(k==="BACK") return '<button class="vk-back" onclick="vkBack()">⌫</button>';
      if(k==="ENTER") return '<button class="vk-enter" onclick="vkEnter()">Aceptar</button>';
      if(k==="CANCEL") return '<button class="vk-cancel" onclick="closeVKB()">Cancelar</button>';
      if(k==="SHIFT") return '<button class="vk-shift" onclick="vkShift()">⇧</button>';
      return `<button onclick="vkType('${k}')">${k}</button>`;
    }).join("")+'</div>'
  ).join("");
}
function vkType(c){ if(!vkbTarget)return; vkbTarget.value+=c; document.getElementById("vkb-display").textContent=vkbTarget.value; }
function vkBack(){ if(!vkbTarget)return; vkbTarget.value=vkbTarget.value.slice(0,-1); document.getElementById("vkb-display").textContent=vkbTarget.value; }
function vkShift(){ vkbShift=!vkbShift; renderVKB(); }
function vkEnter(){ closeVKB(); }

/* === TOAST === */
function toast(msg){ const t=document.getElementById("toast"); t.textContent=msg; t.classList.add("show"); setTimeout(()=>t.classList.remove("show"),2200); }

/* === INIT === */
loadProducts();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    print("=" * 50)
    print("  🐔  Pollería Las Portadas - TPV")
    print("  Abre http://localhost:5000 en tu navegador")
    print("=" * 50)
    app.run(host="0.0.0.0", port=5000, debug=False)
