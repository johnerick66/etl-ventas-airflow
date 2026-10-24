"""ETL diario de ventas: CSV de las tiendas -> validación -> PostgreSQL.

Se dispara a mano con el parámetro `fecha` (AAAA-MM-DD). Cada corrida reemplaza
solo los datos de esa fecha, así que repetirla no duplica filas.
"""

import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from airflow.providers.postgres.hooks.postgres import PostgresHook
from airflow.sdk import Param, dag, get_current_context, task

from ventas.reglas import leer_csv, revisar_calidad, separar_ventas

CARPETA_ENTRADA = Path(os.environ.get("VENTAS_CARPETA_ENTRADA", "/opt/airflow/data/entrada"))
CONEXION = "postgres_datapath"

CREAR_TABLAS = """
CREATE TABLE IF NOT EXISTS ventas_limpias (
    id_venta        INTEGER PRIMARY KEY,
    fecha           DATE NOT NULL,
    region          TEXT NOT NULL,
    producto        TEXT NOT NULL,
    cantidad        INTEGER NOT NULL CHECK (cantidad > 0),
    precio_unitario NUMERIC(10, 2) NOT NULL CHECK (precio_unitario > 0),
    total           NUMERIC(12, 2) NOT NULL,
    cargado_en      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS ventas_rechazadas (
    fecha_archivo DATE NOT NULL,
    linea         INTEGER NOT NULL,
    id_venta      TEXT,
    motivo        TEXT NOT NULL,
    fila_original TEXT NOT NULL,
    PRIMARY KEY (fecha_archivo, linea)
);
CREATE TABLE IF NOT EXISTS resumen_ventas_diario (
    fecha       DATE NOT NULL,
    region      TEXT NOT NULL,
    num_ventas  INTEGER NOT NULL,
    unidades    INTEGER NOT NULL,
    monto_total NUMERIC(14, 2) NOT NULL,
    PRIMARY KEY (fecha, region)
);
"""


@dag(
    dag_id="etl_ventas_diarias",
    description="Carga diaria de ventas desde CSV a PostgreSQL",
    start_date=datetime(2026, 10, 1, tzinfo=timezone.utc),
    schedule=None,
    catchup=False,
    max_active_runs=1,
    params={
        "fecha": Param("2026-10-05", type="string", format="date",
                       description="Fecha del archivo a procesar (AAAA-MM-DD)"),
    },
    default_args={"owner": "equipo-datos", "retries": 1, "retry_delay": timedelta(seconds=15)},
    tags=["laboratorio", "ventas", "etl"],
)
def etl_ventas_diarias():

    @task
    def extraer():
        fecha = get_current_context()["params"]["fecha"]
        date.fromisoformat(fecha)  # falla aquí si no es una fecha válida
        ruta = CARPETA_ENTRADA / f"ventas_{fecha}.csv"
        if not ruta.exists():
            raise FileNotFoundError(f"No existe el archivo {ruta}")
        filas = leer_csv(ruta)
        print(f"Se leyeron {len(filas)} filas de {ruta.name}")
        return {"fecha": fecha, "filas": filas}

    @task
    def transformar(datos):
        limpias, rechazadas = separar_ventas(datos["filas"], datos["fecha"])
        print(f"Válidas: {len(limpias)} | Rechazadas: {len(rechazadas)}")
        for fila in rechazadas:
            print(f"  línea {fila['linea']}: {fila['motivo']}")
        return {
            "fecha": datos["fecha"],
            "leidas": len(datos["filas"]),
            "limpias": limpias,
            "rechazadas": rechazadas,
        }

    # Un archivo malo no se arregla reintentando, por eso aquí no hay retries
    @task(retries=0)
    def validar_calidad(resultado):
        tasa = revisar_calidad(resultado["leidas"], len(resultado["rechazadas"]))
        print(f"Tasa de rechazo: {tasa:.1%}")
        return resultado

    @task
    def cargar(resultado):
        fecha = date.fromisoformat(resultado["fecha"])
        limpias = resultado["limpias"]
        rechazadas = resultado["rechazadas"]

        filas_limpias = [
            (v["id_venta"], fecha, v["region"], v["producto"],
             v["cantidad"], v["precio_unitario"], v["total"])
            for v in limpias
        ]
        filas_rechazadas = [
            (fecha, r["linea"], r["id_venta"], r["motivo"], r["fila_original"])
            for r in rechazadas
        ]

        conn = PostgresHook(postgres_conn_id=CONEXION).get_conn()
        try:
            # Todo va en una sola transacción: si algo falla no queda la fecha a medias
            with conn:
                with conn.cursor() as cur:
                    cur.execute(CREAR_TABLAS)
                    cur.execute("DELETE FROM ventas_limpias WHERE fecha = %s", (fecha,))
                    cur.execute("DELETE FROM ventas_rechazadas WHERE fecha_archivo = %s", (fecha,))
                    cur.executemany(
                        "INSERT INTO ventas_limpias "
                        "(id_venta, fecha, region, producto, cantidad, precio_unitario, total) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        filas_limpias,
                    )
                    if filas_rechazadas:
                        cur.executemany(
                            "INSERT INTO ventas_rechazadas "
                            "(fecha_archivo, linea, id_venta, motivo, fila_original) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            filas_rechazadas,
                        )
                    cur.execute("SELECT COUNT(*) FROM ventas_limpias WHERE fecha = %s", (fecha,))
                    en_tabla = cur.fetchone()[0]
                    if en_tabla != len(limpias):
                        raise ValueError(f"Se esperaban {len(limpias)} filas y hay {en_tabla}")
        finally:
            conn.close()

        print(f"{fecha}: {len(limpias)} ventas cargadas y {len(rechazadas)} rechazadas")
        return {"fecha": resultado["fecha"], "cargadas": len(limpias), "rechazadas": len(rechazadas)}

    @task
    def actualizar_resumen(carga):
        fecha = date.fromisoformat(carga["fecha"])
        hook = PostgresHook(postgres_conn_id=CONEXION)
        hook.run(
            [
                "DELETE FROM resumen_ventas_diario WHERE fecha = %(fecha)s",
                """
                INSERT INTO resumen_ventas_diario (fecha, region, num_ventas, unidades, monto_total)
                SELECT fecha, region, COUNT(*), SUM(cantidad), SUM(total)
                FROM ventas_limpias
                WHERE fecha = %(fecha)s
                GROUP BY fecha, region
                """,
            ],
            parameters={"fecha": fecha},
        )
        filas = hook.get_records(
            "SELECT region, num_ventas, unidades, monto_total FROM resumen_ventas_diario "
            "WHERE fecha = %(fecha)s ORDER BY region",
            parameters={"fecha": fecha},
        )
        for region, num_ventas, unidades, monto in filas:
            print(f"{region:<7} {num_ventas:>3} ventas {unidades:>4} unidades  S/ {monto:>10}")
        return len(filas)

    actualizar_resumen(cargar(validar_calidad(transformar(extraer()))))


etl_ventas_diarias()
