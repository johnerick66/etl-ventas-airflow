"""Reglas de limpieza y validación de los archivos diarios de ventas.

Este módulo no importa nada de Airflow a propósito: así las reglas se pueden
probar con pytest en segundos, sin levantar Docker ni una base de datos.
"""

import csv
import json
import math
import re

COLUMNAS = ["id_venta", "fecha", "region", "producto", "cantidad", "precio_unitario"]
REGIONES = {"norte", "sur", "centro"}

# Si se rechaza más del 20 % de un archivo no se carga nada de ese día
UMBRAL_RECHAZO = 0.20

# Solo dígitos 0-9. int() y float() aceptan cosas como "+3", "1_000", "1e3" o "nan"
SOLO_DIGITOS = re.compile(r"[0-9]+")
DECIMAL_CON_PUNTO = re.compile(r"[0-9]+(\.[0-9]+)?")


def leer_csv(ruta):
    """Lee el archivo y devuelve una lista de diccionarios con los valores en texto.

    Si una línea trae columnas de más, lo que sobra queda en la clave "_sobrantes".
    Si trae de menos, las columnas que faltan quedan en None.
    """
    # utf-8-sig tolera el BOM que agrega Excel al guardar como "CSV UTF-8".
    # strict=True hace fallar el archivo si una comilla queda sin cerrar; sin eso,
    # el lector se come en silencio las líneas que siguen.
    with open(ruta, newline="", encoding="utf-8-sig") as archivo:
        lector = csv.DictReader(archivo, restkey="_sobrantes", strict=True)
        encabezado = lector.fieldnames or []
        faltantes = [col for col in COLUMNAS if col not in encabezado]
        if faltantes:
            raise ValueError(f"Al archivo le faltan columnas: {', '.join(faltantes)}")
        sobrantes = [col for col in encabezado if col not in COLUMNAS]
        if sobrantes:
            raise ValueError(f"Columnas que no se esperan: {', '.join(sobrantes)}")
        return [dict(fila) for fila in lector]


def _texto(fila, columna):
    return (fila.get(columna) or "").strip()


def _entero_positivo(valor):
    if not SOLO_DIGITOS.fullmatch(valor):
        return None
    try:
        numero = int(valor)
    except ValueError:  # Python no convierte textos de más de 4300 dígitos
        return None
    return numero if numero > 0 else None


def _precio_positivo(valor):
    if not DECIMAL_CON_PUNTO.fullmatch(valor):
        return None
    # se redondea antes de comparar: "0.004" termina siendo 0.00
    precio = round(float(valor), 2)
    # un número de cientos de dígitos se vuelve inf al pasar a float
    return precio if math.isfinite(precio) and precio > 0 else None


def validar_fila(fila, fecha):
    """Aplica las reglas a una fila del CSV.

    Devuelve (registro, None) si la fila es válida o (None, motivo) si no lo es.
    Las reglas se revisan en orden y se reporta solo el primer problema.
    """
    # Un precio 45,50 sin comillas parte la fila en siete columnas
    if "_sobrantes" in fila or any(fila.get(col) is None for col in COLUMNAS):
        return None, "columnas_incorrectas"

    id_venta = _entero_positivo(_texto(fila, "id_venta"))
    if id_venta is None:
        return None, "id_invalido"

    if _texto(fila, "fecha") != fecha:
        return None, "fecha_distinta"

    region = _texto(fila, "region").lower()
    if region not in REGIONES:
        return None, "region_invalida"

    producto = _texto(fila, "producto")
    if not producto:
        return None, "producto_vacio"

    cantidad = _entero_positivo(_texto(fila, "cantidad"))
    if cantidad is None:
        return None, "cantidad_invalida"

    precio = _precio_positivo(_texto(fila, "precio_unitario"))
    if precio is None:
        return None, "precio_invalido"

    registro = {
        "id_venta": id_venta,
        "fecha": fecha,
        "region": region.capitalize(),
        "producto": producto.capitalize(),
        "cantidad": cantidad,
        "precio_unitario": precio,
        "total": round(cantidad * precio, 2),
    }
    return registro, None


def separar_ventas(filas, fecha):
    """Separa las filas en (limpias, rechazadas).

    Si un id_venta se repite, la primera aparición se evalúa como cualquier otra
    fila y las siguientes se rechazan como id_duplicado, aunque la primera haya
    sido rechazada por otro motivo.
    """
    limpias = []
    rechazadas = []
    ids_vistos = set()

    # la línea 1 del archivo es el encabezado
    for linea, fila in enumerate(filas, start=2):
        registro, motivo = validar_fila(fila, fecha)

        # El duplicado es la regla 3: va después de columnas e id, y le gana
        # a cualquier problema de las reglas 4 a 8.
        # Se compara como número: "070001" y "70001" son el mismo id.
        id_venta = _entero_positivo(_texto(fila, "id_venta"))
        if motivo not in ("columnas_incorrectas", "id_invalido") and id_venta in ids_vistos:
            registro, motivo = None, "id_duplicado"

        if id_venta is not None:
            ids_vistos.add(id_venta)

        if registro:
            limpias.append(registro)
        else:
            rechazadas.append({
                "linea": linea,
                "id_venta": _texto(fila, "id_venta") or None,
                "motivo": motivo,
                "fila_original": json.dumps(fila, ensure_ascii=False),
            })

    return limpias, rechazadas


def revisar_calidad(total_leidas, total_rechazadas, umbral=UMBRAL_RECHAZO):
    """Devuelve la tasa de rechazo o lanza ValueError si el archivo no sirve."""
    if total_leidas == 0:
        raise ValueError("El archivo no trae ninguna venta")
    tasa = total_rechazadas / total_leidas
    if tasa > umbral:
        raise ValueError(
            f"Se rechazó el {tasa:.1%} de las filas y el máximo es {umbral:.0%}. "
            "No se carga nada de esta fecha."
        )
    return tasa
