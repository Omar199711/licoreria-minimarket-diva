# -*- coding: utf-8 -*-
"""
Sistema de Inventario - Licorería Minimarket Diva
Backend Flask con base de datos SQLite (persistente).
"""
from flask import Flask, request, jsonify, session, render_template
import sqlite3
import datetime
import os

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "licoreria_los_olivos_2025_clave_dev")

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "licoreria.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db_if_needed():
    """Si la base no existe, la crea vacía con el esquema (usuarios por defecto)."""
    if os.path.exists(DB_PATH):
        return
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS usuarios (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usuario TEXT UNIQUE NOT NULL,
        contrasena TEXT NOT NULL,
        rol TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo_barras TEXT,
        nombre TEXT NOT NULL,
        precio_compra REAL DEFAULT 0,
        precio_venta REAL DEFAULT 0,
        stock REAL DEFAULT 0,
        proveedor TEXT,
        categoria TEXT DEFAULT ''
    );
    CREATE TABLE IF NOT EXISTS movimientos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT NOT NULL,
        tipo TEXT NOT NULL,
        producto TEXT,
        cantidad TEXT
    );
    CREATE TABLE IF NOT EXISTS turnos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT NOT NULL,
        tipo_evento TEXT NOT NULL,
        monto REAL
    );
    CREATE TABLE IF NOT EXISTS cierres (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT NOT NULL,
        monto_sistema REAL,
        monto_declarado REAL,
        diferencia REAL,
        estado TEXT
    );
    CREATE TABLE IF NOT EXISTS cuadre_productos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fecha TEXT NOT NULL,
        usuario TEXT NOT NULL,
        producto_id INTEGER,
        nombre_producto TEXT,
        stock_sistema REAL,
        stock_fisico REAL,
        diferencia REAL,
        estado TEXT
    );
    """)
    conn.execute(
        "INSERT INTO usuarios (usuario, contrasena, rol) VALUES (?,?,?)",
        ("admin", "admin123", "Administrador")
    )
    conn.execute(
        "INSERT INTO usuarios (usuario, contrasena, rol) VALUES (?,?,?)",
        ("cajero", "cajero123", "Cajero")
    )
    conn.execute(
        "INSERT INTO usuarios (usuario, contrasena, rol) VALUES (?,?,?)",
        ("super", "super123", "Supervisor")
    )
    conn.commit()
    conn.close()


init_db_if_needed()


def registrar_movimiento(conn, usuario, tipo, producto, cantidad):
    fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn.execute(
        "INSERT INTO movimientos (fecha, usuario, tipo, producto, cantidad) VALUES (?,?,?,?,?)",
        (fecha, usuario, tipo, str(producto), str(cantidad))
    )


def calcular_ventas_turno(conn, usuario):
    """Suma el total de ventas (cantidad x precio_venta) hechas por el usuario,
    desde su última apertura de turno."""
    row = conn.execute(
        "SELECT fecha FROM turnos WHERE usuario=? AND tipo_evento='APERTURA' ORDER BY id DESC LIMIT 1",
        (usuario,)
    ).fetchone()
    fecha_apertura = row["fecha"] if row else "0000-00-00 00:00"

    movs = conn.execute(
        "SELECT producto, cantidad FROM movimientos WHERE usuario=? AND tipo='Venta' AND fecha >= ?",
        (usuario, fecha_apertura)
    ).fetchall()

    total = 0.0
    for m in movs:
        prod = conn.execute(
            "SELECT precio_venta FROM productos WHERE nombre=?", (m["producto"],)
        ).fetchone()
        if prod:
            try:
                total += prod["precio_venta"] * float(m["cantidad"])
            except (ValueError, TypeError):
                pass
    return round(total, 2)


# ─── RUTAS WEB ────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")


# ─── LOGIN ────────────────────────────────────────────────────

@app.route("/api/login", methods=["POST"])
def login():
    data = request.json
    usuario = (data.get("usuario") or "").strip()
    contrasena = (data.get("contrasena") or "").strip()

    conn = get_db()
    row = conn.execute(
        "SELECT * FROM usuarios WHERE usuario=? AND contrasena=?",
        (usuario, contrasena)
    ).fetchone()
    conn.close()

    if row:
        session["usuario"] = row["usuario"]
        session["rol"] = row["rol"]
        return jsonify({"ok": True, "usuario": row["usuario"], "rol": row["rol"]})
    return jsonify({"ok": False, "msg": "Usuario o contraseña incorrectos."})


@app.route("/api/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"ok": True})


def requiere_rol(*roles_permitidos):
    rol = session.get("rol")
    return rol in roles_permitidos


# ─── INVENTARIO ───────────────────────────────────────────────

@app.route("/api/inventario", methods=["GET"])
def inventario():
    conn = get_db()
    rows = conn.execute(
        "SELECT id, codigo_barras, nombre, precio_compra, precio_venta, stock, proveedor, categoria FROM productos ORDER BY nombre"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/registrar_producto", methods=["POST"])
def registrar_producto():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    nombre = (data.get("nombre") or "").strip()
    codigo_barras = (data.get("codigo_barras") or "").strip()
    stock = data.get("stock", 0)
    precio_compra = data.get("precio_compra", 0)
    precio_venta = data.get("precio_venta", 0)
    categoria = (data.get("categoria") or "").strip()
    proveedor = (data.get("proveedor") or "").strip()

    if not nombre:
        return jsonify({"ok": False, "msg": "El nombre es obligatorio."})

    conn = get_db()
    existe = conn.execute(
        "SELECT id FROM productos WHERE LOWER(nombre)=LOWER(?)", (nombre,)
    ).fetchone()
    if existe:
        conn.close()
        return jsonify({"ok": False, "msg": f"El producto '{nombre}' ya existe."})

    try:
        stock_f = float(stock or 0)
        pc_f = float(precio_compra or 0)
        pv_f = float(precio_venta or 0)
    except ValueError:
        conn.close()
        return jsonify({"ok": False, "msg": "Stock y precios deben ser numéricos."})

    conn.execute(
        "INSERT INTO productos (codigo_barras, nombre, precio_compra, precio_venta, stock, proveedor, categoria) VALUES (?,?,?,?,?,?,?)",
        (codigo_barras, nombre, pc_f, pv_f, stock_f, proveedor, categoria)
    )
    registrar_movimiento(conn, usuario, "Alta", nombre, stock_f)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": f"Producto '{nombre}' registrado correctamente."})


@app.route("/api/modificar_stock", methods=["POST"])
def modificar_stock():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    nombre = (data.get("nombre") or "").strip()
    cantidad = data.get("cantidad")

    try:
        cantidad_f = float(cantidad)
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Cantidad inválida."})

    conn = get_db()
    prod = conn.execute("SELECT * FROM productos WHERE LOWER(nombre)=LOWER(?)", (nombre,)).fetchone()
    if not prod:
        conn.close()
        return jsonify({"ok": False, "msg": "Producto no encontrado."})

    conn.execute("UPDATE productos SET stock=? WHERE id=?", (cantidad_f, prod["id"]))
    registrar_movimiento(conn, usuario, "ModStock", prod["nombre"], cantidad_f)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": f"Stock de '{prod['nombre']}' actualizado a {cantidad_f}."})


@app.route("/api/buscar_producto", methods=["GET"])
def buscar_producto():
    nombre = (request.args.get("nombre") or "").strip()
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM productos WHERE LOWER(nombre) LIKE LOWER(?) LIMIT 1",
        (f"%{nombre}%",)
    ).fetchone()
    conn.close()
    if row:
        return jsonify({"ok": True, "producto": dict(row)})
    return jsonify({"ok": False, "msg": "Producto no encontrado."})


# ─── VENTAS ───────────────────────────────────────────────────

@app.route("/api/registrar_venta", methods=["POST"])
def registrar_venta():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    nombre = (data.get("nombre") or "").strip()
    try:
        cantidad = int(data.get("cantidad", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Cantidad inválida."})

    if cantidad <= 0:
        return jsonify({"ok": False, "msg": "La cantidad debe ser mayor a 0."})

    conn = get_db()
    prod = conn.execute("SELECT * FROM productos WHERE LOWER(nombre)=LOWER(?)", (nombre,)).fetchone()
    if not prod:
        conn.close()
        return jsonify({"ok": False, "msg": "Producto no encontrado."})

    stock_actual = prod["stock"]
    if stock_actual < cantidad:
        conn.close()
        return jsonify({"ok": False, "msg": f"Stock insuficiente. Disponible: {stock_actual}"})

    nuevo_stock = stock_actual - cantidad
    total = round(cantidad * prod["precio_venta"], 2)

    conn.execute("UPDATE productos SET stock=? WHERE id=?", (nuevo_stock, prod["id"]))
    registrar_movimiento(conn, usuario, "Venta", prod["nombre"], cantidad)
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "producto": prod["nombre"],
        "cantidad": cantidad,
        "total": total,
        "stock_restante": nuevo_stock
    })


# ─── TURNOS ───────────────────────────────────────────────────

@app.route("/api/abrir_turno", methods=["POST"])
def abrir_turno():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    try:
        monto = float(data.get("monto", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Monto inválido."})

    fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    conn = get_db()
    conn.execute(
        "INSERT INTO turnos (fecha, usuario, tipo_evento, monto) VALUES (?,?,?,?)",
        (fecha, usuario, "APERTURA", monto)
    )
    registrar_movimiento(conn, usuario, "AperturaTurno", "Caja", monto)
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": f"Turno abierto a las {fecha}."})


@app.route("/api/cerrar_turno", methods=["POST"])
def cerrar_turno():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    try:
        monto_declarado = float(data.get("monto", 0))
    except (ValueError, TypeError):
        return jsonify({"ok": False, "msg": "Monto inválido."})

    conn = get_db()
    monto_sistema = calcular_ventas_turno(conn, usuario)
    diferencia = round(monto_declarado - monto_sistema, 2)
    estado = "OK" if diferencia == 0 else ("SOBRANTE" if diferencia > 0 else "FALTANTE")
    fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")

    conn.execute(
        "INSERT INTO cierres (fecha, usuario, monto_sistema, monto_declarado, diferencia, estado) VALUES (?,?,?,?,?,?)",
        (fecha, usuario, monto_sistema, monto_declarado, diferencia, estado)
    )
    conn.execute(
        "INSERT INTO turnos (fecha, usuario, tipo_evento, monto) VALUES (?,?,?,?)",
        (fecha, usuario, "CIERRE", monto_declarado)
    )
    registrar_movimiento(conn, usuario, "CierreTurno", "Caja", diferencia)
    conn.commit()
    conn.close()

    return jsonify({
        "ok": True,
        "cajero": usuario,
        "fecha": fecha,
        "monto_sistema": monto_sistema,
        "monto_declarado": monto_declarado,
        "diferencia": diferencia,
        "estado": estado
    })


# ─── CUADRE DE PRODUCTOS ──────────────────────────────────────

@app.route("/api/cuadre_productos", methods=["POST"])
def cuadre_productos():
    if "usuario" not in session:
        return jsonify({"ok": False, "msg": "Sesión expirada."})
    data = request.json
    usuario = session.get("usuario")
    conteos = data.get("conteos", {})  # { "nombre_producto": conteo_fisico }

    conn = get_db()
    fecha = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    descuadres = []

    for nombre, conteo_fisico in conteos.items():
        prod = conn.execute("SELECT * FROM productos WHERE nombre=?", (nombre,)).fetchone()
        if not prod:
            continue
        try:
            conteo_fisico = float(conteo_fisico)
        except (ValueError, TypeError):
            continue

        stock_sistema = prod["stock"]
        diferencia = conteo_fisico - stock_sistema
        if diferencia != 0:
            estado = "FALTANTE" if diferencia < 0 else "SOBRANTE"
            descuadres.append({
                "nombre": prod["nombre"],
                "sistema": stock_sistema,
                "fisico": conteo_fisico,
                "diferencia": diferencia,
                "estado": estado
            })
            conn.execute(
                "INSERT INTO cuadre_productos (fecha, usuario, producto_id, nombre_producto, stock_sistema, stock_fisico, diferencia, estado) VALUES (?,?,?,?,?,?,?,?)",
                (fecha, usuario, prod["id"], prod["nombre"], stock_sistema, conteo_fisico, diferencia, estado)
            )

    conn.commit()
    conn.close()
    return jsonify({"ok": True, "descuadres": descuadres})


# ─── REPORTES ─────────────────────────────────────────────────

@app.route("/api/reporte_descuadres", methods=["GET"])
def reporte_descuadres():
    conn = get_db()
    caja = conn.execute(
        "SELECT * FROM cierres WHERE estado != 'OK' ORDER BY id DESC"
    ).fetchall()
    productos = conn.execute(
        "SELECT * FROM cuadre_productos WHERE estado != 'OK' ORDER BY id DESC"
    ).fetchall()
    conn.close()

    return jsonify({
        "caja": [dict(r) for r in caja],
        "productos": [dict(r) for r in productos]
    })


@app.route("/api/historial", methods=["GET"])
def historial():
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM movimientos ORDER BY id DESC LIMIT 500"
    ).fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


# ─── USUARIOS (solo Administrador) ────────────────────────────

@app.route("/api/usuarios", methods=["GET"])
def listar_usuarios():
    conn = get_db()
    rows = conn.execute("SELECT usuario, rol FROM usuarios ORDER BY usuario").fetchall()
    conn.close()
    return jsonify([dict(r) for r in rows])


@app.route("/api/crear_usuario", methods=["POST"])
def crear_usuario():
    if not requiere_rol("Administrador"):
        return jsonify({"ok": False, "msg": "No tiene permisos para esta acción."})
    data = request.json
    nuevo = (data.get("usuario") or "").strip()
    clave = (data.get("contrasena") or "").strip()
    rol = (data.get("rol") or "Cajero").strip()

    if not nuevo or not clave:
        return jsonify({"ok": False, "msg": "Usuario y contraseña son obligatorios."})

    conn = get_db()
    existe = conn.execute("SELECT id FROM usuarios WHERE usuario=?", (nuevo,)).fetchone()
    if existe:
        conn.close()
        return jsonify({"ok": False, "msg": f"El usuario '{nuevo}' ya existe."})

    conn.execute(
        "INSERT INTO usuarios (usuario, contrasena, rol) VALUES (?,?,?)",
        (nuevo, clave, rol)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": f"Usuario '{nuevo}' creado correctamente."})


@app.route("/api/editar_usuario", methods=["POST"])
def editar_usuario():
    if not requiere_rol("Administrador"):
        return jsonify({"ok": False, "msg": "No tiene permisos para esta acción."})
    data = request.json
    buscar = (data.get("usuario") or "").strip()
    nueva_clave = (data.get("contrasena") or "").strip()
    nuevo_rol = (data.get("rol") or "").strip()

    conn = get_db()
    row = conn.execute("SELECT * FROM usuarios WHERE usuario=?", (buscar,)).fetchone()
    if not row:
        conn.close()
        return jsonify({"ok": False, "msg": "Usuario no encontrado."})

    clave_final = nueva_clave if nueva_clave else row["contrasena"]
    rol_final = nuevo_rol if nuevo_rol else row["rol"]
    conn.execute(
        "UPDATE usuarios SET contrasena=?, rol=? WHERE usuario=?",
        (clave_final, rol_final, buscar)
    )
    conn.commit()
    conn.close()
    return jsonify({"ok": True, "msg": f"Usuario '{buscar}' actualizado correctamente."})


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=True)
