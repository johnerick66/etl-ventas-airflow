import csv

import pytest

from ventas.reglas import (
    UMBRAL_RECHAZO,
    leer_csv,
    revisar_calidad,
    separar_ventas,
    validar_fila,
)

FECHA = "2026-10-05"
ENCABEZADO = "id_venta,fecha,region,producto,cantidad,precio_unitario\n"


def fila(**cambios):
    base = {
        "id_venta": "70001",
        "fecha": FECHA,
        "region": "norte",
        "producto": "Mouse",
        "cantidad": "3",
        "precio_unitario": "45.50",
    }
    base.update(cambios)
    return base


# --- validar_fila -------------------------------------------------------

def test_fila_valida_calcula_el_total():
    registro, motivo = validar_fila(fila(), FECHA)
    assert motivo is None
    assert registro["id_venta"] == 70001
    assert registro["cantidad"] == 3
    assert registro["total"] == 136.5


def test_normaliza_region_y_producto():
    registro, _ = validar_fila(fila(region="  SUR ", producto=" AURICULARES"), FECHA)
    assert registro["region"] == "Sur"
    assert registro["producto"] == "Auriculares"


@pytest.mark.parametrize(
    "cambios, motivo_esperado",
    [
        ({"id_venta": ""}, "id_invalido"),
        ({"id_venta": "abc"}, "id_invalido"),
        ({"id_venta": "-5"}, "id_invalido"),
        ({"id_venta": "+5"}, "id_invalido"),
        ({"fecha": "2026-10-04"}, "fecha_distinta"),
        ({"region": "Lima"}, "region_invalida"),
        ({"region": ""}, "region_invalida"),
        ({"producto": "   "}, "producto_vacio"),
        ({"cantidad": ""}, "cantidad_invalida"),
        ({"cantidad": "0"}, "cantidad_invalida"),
        ({"cantidad": "-2"}, "cantidad_invalida"),
        ({"cantidad": "dos"}, "cantidad_invalida"),
        ({"cantidad": "1.5"}, "cantidad_invalida"),
        ({"cantidad": "+3"}, "cantidad_invalida"),
        ({"precio_unitario": ""}, "precio_invalido"),
        ({"precio_unitario": "0.00"}, "precio_invalido"),
        ({"precio_unitario": "0.004"}, "precio_invalido"),  # redondeado queda en 0
        ({"precio_unitario": "45,50"}, "precio_invalido"),
        ({"precio_unitario": "nan"}, "precio_invalido"),
        ({"precio_unitario": "1e3"}, "precio_invalido"),
    ],
)
def test_motivos_de_rechazo(cambios, motivo_esperado):
    registro, motivo = validar_fila(fila(**cambios), FECHA)
    assert registro is None
    assert motivo == motivo_esperado


def test_precio_sin_decimales_es_valido():
    registro, _ = validar_fila(fila(precio_unitario="699"), FECHA)
    assert registro["precio_unitario"] == 699.0


def test_se_reporta_el_primer_problema():
    # región y cantidad están mal, pero la región se revisa antes
    _, motivo = validar_fila(fila(region="Oeste", cantidad="dos"), FECHA)
    assert motivo == "region_invalida"


def test_fila_con_columnas_de_menos():
    # DictReader deja None cuando una línea trae menos columnas
    _, motivo = validar_fila(fila(precio_unitario=None), FECHA)
    assert motivo == "columnas_incorrectas"


# --- separar_ventas -----------------------------------------------------

def test_duplicado_conserva_la_primera_aparicion():
    filas = [fila(), fila(cantidad="9"), fila(id_venta="70002")]
    limpias, rechazadas = separar_ventas(filas, FECHA)
    assert [v["id_venta"] for v in limpias] == [70001, 70002]
    assert limpias[0]["cantidad"] == 3
    assert len(rechazadas) == 1
    assert rechazadas[0]["motivo"] == "id_duplicado"


def test_duplicado_aunque_la_primera_aparicion_se_rechace():
    _, rechazadas = separar_ventas([fila(cantidad=""), fila()], FECHA)
    assert [r["motivo"] for r in rechazadas] == ["cantidad_invalida", "id_duplicado"]


def test_duplicado_se_revisa_antes_que_lo_demas():
    _, rechazadas = separar_ventas([fila(), fila(region="Lima")], FECHA)
    assert rechazadas[0]["motivo"] == "id_duplicado"


def test_ids_se_comparan_como_numero():
    _, rechazadas = separar_ventas([fila(id_venta="70001"), fila(id_venta="070001")], FECHA)
    assert rechazadas[0]["motivo"] == "id_duplicado"


def test_columnas_incorrectas_le_gana_al_duplicado():
    # las reglas 1 y 2 van antes que la del duplicado
    _, rechazadas = separar_ventas([fila(), fila(precio_unitario=None)], FECHA)
    assert rechazadas[0]["motivo"] == "columnas_incorrectas"


def test_rechazadas_guardan_linea_y_fila_original():
    filas = [fila(), fila(id_venta="70002", region="Lima")]
    _, rechazadas = separar_ventas(filas, FECHA)
    assert rechazadas[0]["linea"] == 3  # la línea 1 es el encabezado
    assert rechazadas[0]["id_venta"] == "70002"
    assert "Lima" in rechazadas[0]["fila_original"]


def test_ningun_registro_se_pierde():
    filas = [fila(), fila(id_venta=""), fila(id_venta="70003", cantidad="0"), fila()]
    limpias, rechazadas = separar_ventas(filas, FECHA)
    assert len(limpias) + len(rechazadas) == len(filas)


# --- revisar_calidad ----------------------------------------------------

def test_calidad_acepta_hasta_el_umbral():
    assert revisar_calidad(10, 2) == pytest.approx(0.2)
    assert revisar_calidad(46, 5) < UMBRAL_RECHAZO


def test_calidad_rechaza_sobre_el_umbral():
    with pytest.raises(ValueError):
        revisar_calidad(41, 24)


def test_calidad_rechaza_archivo_vacio():
    with pytest.raises(ValueError):
        revisar_calidad(0, 0)


# --- leer_csv -----------------------------------------------------------

def test_leer_csv_exige_las_columnas(tmp_path):
    ruta = tmp_path / "ventas.csv"
    ruta.write_text(
        "id_venta,fecha,region,producto,cantidad\n1,2026-10-05,norte,Mouse,2\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="precio_unitario"):
        leer_csv(ruta)


def test_leer_csv_respeta_valores_entre_comillas(tmp_path):
    ruta = tmp_path / "ventas.csv"
    ruta.write_text(ENCABEZADO + '1,2026-10-05,sur,Mouse,2,"45,50"\n', encoding="utf-8")
    filas = leer_csv(ruta)
    assert filas[0]["precio_unitario"] == "45,50"


def test_coma_sin_comillas_no_pasa_con_otro_precio(tmp_path):
    # sin comillas, 45,50 se lee como precio "45" y una columna extra "50"
    ruta = tmp_path / "ventas.csv"
    ruta.write_text(ENCABEZADO + "1,2026-10-05,sur,Mouse,2,45,50\n", encoding="utf-8")
    limpias, rechazadas = separar_ventas(leer_csv(ruta), FECHA)
    assert limpias == []
    assert rechazadas[0]["motivo"] == "columnas_incorrectas"


def test_leer_csv_tolera_el_bom_de_excel(tmp_path):
    ruta = tmp_path / "ventas.csv"
    ruta.write_text(ENCABEZADO + "1,2026-10-05,sur,Mouse,2,45.50\n", encoding="utf-8-sig")
    assert leer_csv(ruta)[0]["id_venta"] == "1"


def test_leer_csv_rechaza_columnas_de_mas_en_el_encabezado(tmp_path):
    # con una columna extra en el encabezado, el "50" de 45,50 caería ahí sin avisar
    ruta = tmp_path / "ventas.csv"
    contenido = ENCABEZADO.strip() + ",obs\n1,2026-10-05,sur,Mouse,2,45,50\n"
    ruta.write_text(contenido, encoding="utf-8")
    with pytest.raises(ValueError, match="obs"):
        leer_csv(ruta)


def test_leer_csv_falla_con_una_comilla_sin_cerrar(tmp_path):
    ruta = tmp_path / "ventas.csv"
    ruta.write_text(
        ENCABEZADO + '1,2026-10-05,sur,"Mouse,2,45.50\n2,2026-10-05,sur,Mouse,1,45.50\n',
        encoding="utf-8",
    )
    with pytest.raises(csv.Error):
        leer_csv(ruta)
