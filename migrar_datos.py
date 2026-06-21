# -*- coding: utf-8 -*-
"""
Script de migración: convierte data/inventario_original.csv (formato POS real,
9 columnas) a la base de datos SQLite del sistema, generando IDs autoincrementales
y conservando el código de barras original como campo aparte.

Formato de entrada (por línea, separado por comas):
  codigo_barras, nombre, precio_compra, precio_venta, (vacio), stock, 0, 0, proveedor

Casos especiales manejados:
  - Nombres de producto que contienen comas (ej: "BOTELLA VACIA... 630ML, 1LITRO")
  - Precios con coma de miles (ej: "S/1,100.00")
  - Stocks "infinitos" (1000000.00 / 999999.00) se interpretan como productos
    sin control de stock estricto, pero se guardan tal cual (no se trunca el dato).
"""
import csv
import re
import sqlite3
import os

BASE = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE, "data", "inventario_original.csv")
DB_PATH = os.path.join(BASE, "data", "licoreria.db")


def limpiar_precio(valor):
    """Convierte 'S/35.00' o 'S/1,100.00' -> float 35.0 / 1100.0"""
    valor = valor.strip().replace("S/", "").replace(" ", "")
    valor = valor.replace(",", "")  # quita comas de miles
    try:
        return float(valor)
    except ValueError:
        return 0.0


def reparar_linea(linea):
    """
    Repara líneas con comas extra dentro del nombre o del precio.

    Estrategia robusta: una línea válida siempre tiene esta forma fija en
    los extremos:
        [0]    codigo_barras
        [1..N] nombre + precio_compra + precio_venta + vacio (variable, puede tener comas de más)
        [-4]   stock
        [-3]   "0"
        [-2]   "0"
        [-1]   proveedor

    Los últimos 4 campos y el primero son siempre fijos y reconocibles.
    Todo lo que sobra en el medio se reconstruye así: cualquier campo que
    empiece con "S/" es un precio (puede venir partido por una coma de
    miles, ej: "S/1" + "100.00" -> "S/1,100.00"); todo lo demás antes del
    primer "S/" es parte del nombre.
    """
    campos = linea.strip().split(",")
    if len(campos) == 9:
        return campos

    codigo = campos[0]
    proveedor = campos[-1]
    cero2 = campos[-2]
    cero1 = campos[-3]
    stock = campos[-4]
    medio = campos[1:-4]  # nombre + precio_compra + precio_venta + vacio, con posibles comas extra

    nombre_partes = []
    precios = []
    i = 0
    while i < len(medio):
        campo = medio[i]
        if campo.strip().startswith("S/"):
            valor = campo
            if i + 1 < len(medio) and re.match(r"^\d+(\.\d+)?$", medio[i + 1].strip()):
                valor = campo + "," + medio[i + 1]
                i += 1
            precios.append(valor)
        else:
            nombre_partes.append(campo)
        i += 1

    nombre = ",".join(nombre_partes).strip()
    precio_compra = precios[0] if len(precios) > 0 else "S/0.00"
    precio_venta = precios[1] if len(precios) > 1 else "S/0.00"
    vacio = precios[2] if len(precios) > 2 else "S/0.00"

    return [codigo, nombre, precio_compra, precio_venta, vacio, stock, cero1, cero2, proveedor]


def cargar_csv():
    productos = []
    errores = []
    with open(CSV_PATH, "r", encoding="utf-8") as f:
        lineas = f.readlines()

    for num, linea in enumerate(lineas, start=1):
        linea = linea.rstrip("\n")
        if not linea.strip():
            continue
        campos = reparar_linea(linea)
        if len(campos) != 9:
            errores.append((num, linea[:90]))
            continue

        codigo_barras = campos[0].strip()
        nombre = campos[1].strip()
        precio_compra = limpiar_precio(campos[2])
        precio_venta = limpiar_precio(campos[3])
        # campos[4] vacío / no usado
        try:
            stock = float(campos[5].strip())
        except ValueError:
            stock = 0.0
        proveedor = campos[8].strip()

        productos.append({
            "codigo_barras": codigo_barras,
            "nombre": nombre,
            "precio_compra": precio_compra,
            "precio_venta": precio_venta,
            "stock": stock,
            "proveedor": proveedor,
        })

    return productos, errores


def crear_esquema(conn):
    cur = conn.cursor()
    cur.executescript("""
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
    conn.commit()


def poblar_usuarios(conn):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM usuarios")
    if cur.fetchone()[0] > 0:
        print("Usuarios ya existen, no se sobreescriben.")
        return
    usuarios = [
        ("admin", "admin123", "Administrador"),
        ("cajero", "cajero123", "Cajero"),
        ("super", "super123", "Supervisor"),
    ]
    cur.executemany(
        "INSERT INTO usuarios (usuario, contrasena, rol) VALUES (?, ?, ?)",
        usuarios
    )
    conn.commit()
    print(f"Usuarios creados: {len(usuarios)}")


def poblar_productos(conn, productos):
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM productos")
    if cur.fetchone()[0] > 0:
        print("Productos ya existen en la base. Si quieres recargar, borra data/licoreria.db primero.")
        return

    for p in productos:
        cur.execute("""
            INSERT INTO productos (codigo_barras, nombre, precio_compra, precio_venta, stock, proveedor, categoria)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            p["codigo_barras"], p["nombre"], p["precio_compra"],
            p["precio_venta"], p["stock"], p["proveedor"], ""
        ))
    conn.commit()
    print(f"Productos migrados: {len(productos)}")


def main():
    productos, errores = cargar_csv()
    print(f"Productos parseados correctamente: {len(productos)}")
    if errores:
        print(f"\nLineas que no se pudieron procesar ({len(errores)}):")
        for num, txt in errores:
            print(f"  Linea {num}: {txt}")
    else:
        print("Sin errores de parseo. Todas las lineas se procesaron correctamente.")

    conn = sqlite3.connect(DB_PATH)
    crear_esquema(conn)
    poblar_usuarios(conn)
    poblar_productos(conn, productos)
    conn.close()
    print(f"\nBase de datos creada en: {DB_PATH}")


if __name__ == "__main__":
    main()
