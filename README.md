# ETL de ventas diarias con Airflow

Laboratorio de los módulos 6 y 7: archivos CSV de ventas -> validación -> PostgreSQL, orquestado con Airflow 3.3.2 y con pruebas automáticas en GitHub Actions.

Esta es la versión resuelta. El enunciado está en la carpeta principal del laboratorio y la guía paso a paso en `solucion/Solución paso a paso.pdf`.

## Cómo levantarlo

```bash
python3 preparar_entorno.py
docker compose build
docker compose up airflow-init
docker compose up -d --wait
```

La interfaz queda en http://localhost:8080 (usuario `airflow`, clave `airflow`). El DAG se llama `etl_ventas_diarias` y se dispara con el parámetro `fecha`.

## Cómo correr las pruebas

Dentro del contenedor, sin instalar nada:

```bash
docker compose exec airflow-scheduler pytest
```

O en un entorno virtual con Python 3.12, igual que en GitHub Actions:

```bash
python3.12 -m venv .venv
source .venv/bin/activate        # en Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt --constraint constraints-3.12.txt
pytest -v
```

## Respuestas

1. **¿Por qué falló el 7 de octubre?** Las cajas del sur y del centro exportaron el precio con coma decimal (`"45,50"`). Esas 22 filas no pasan la regla del precio y, con una cantidad escrita como texto y una región inexistente, suman 24 rechazos de 41 (58,5 %), así que `validar_calidad` cortó la corrida. Sin ese control se habrían cargado solo las 17 ventas del norte (S/ 24 821,70 en lugar de S/ 65 642,70), y el reporte de ese día habría mostrado una caída de ventas que nunca ocurrió.

2. **¿Por qué repetir una fecha no duplica?** `cargar` borra lo que haya de esa fecha y vuelve a insertar, todo en la misma transacción. Correr la misma fecha dos veces deja el mismo resultado. Si solo hiciera `INSERT`, la segunda corrida chocaría con la clave primaria de `id_venta`; sin clave primaria, duplicaría todas las ventas del día sin dar ningún error.

3. **¿Por qué el CI no necesita PostgreSQL?** Las reglas están en un módulo que no depende de Airflow, y las pruebas del DAG solo lo cargan con `DagBag` para revisar su estructura: que importe sin errores, que no tenga ciclos, sus tareas y su configuración. Ninguna prueba ejecuta `cargar` ni `actualizar_resumen`, así que el CI no comprueba el SQL, la transacción ni la conexión `postgres_datapath`.

4. **¿Por qué `validar_calidad` no reintenta?** Si un archivo viene mal, reintentar 15 segundos después da el mismo resultado y solo demora el aviso. En cambio `extraer` puede fallar por algo pasajero (el archivo todavía se está copiando, un disco de red lento) y ahí un reintento sí puede resolverlo.
