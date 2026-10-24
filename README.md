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

1. **¿Por qué falló el 7 de octubre?** Las sedes del Sur y Centro guardaron los montos usando una coma decimal (`"45,50"`), lo que invalidó 22 registros. Al sumarse un error de formato en cantidad y una región no válida, se alcanzó un 58.5% de registros corruptos (24 fallas de 41 totales), provocando que la tarea `validar_calidad` detuviera el flujo. Sin este filtro, solo se habrían guardado las 17 ventas del Norte (S/ 24,821.70 en vez de S/ 65,642.70), generando un reporte con una falsa caída de ingresos en el negocio.

2. **¿Por qué repetir una fecha no duplica?** La función `cargar` ejecuta de manera atómica, dentro de una única transacción SQL, el borrado de cualquier registro previo de esa fecha específica antes de proceder con la nueva inserción. Si el pipeline hiciera únicamente un `INSERT`, la reejecución colisionaría de inmediato con la llave primaria de `id_venta`. De no contar con restricciones de integridad, se duplicarían silenciosamente todos los datos en las tablas de destino.

3. **¿Por qué el CI no necesita PostgreSQL?** Las reglas de validación se aislaron en un módulo de Python puro ajeno al motor de base de datos. Además, el entorno de integración continua solo emplea `DagBag` para auditar la estructura y configuración formal del archivo del DAG (verificando que compile, que no contenga bucles y que declare bien sus tareas). Al no ejecutarse las tareas de escritura `cargar` ni `actualizar_resumen` durante el pipeline de CI, jamás se invoca la conexión física a `postgres_datapath`.

4. **¿Por qué `validar_calidad` no reintenta?** Si un archivo de origen contiene errores estructurales o formatos de datos corruptos, reintentar la operación minutos después arrojará idéntico resultado y retrasará la alerta al equipo de ingeniería. En contraste, la etapa de `extraer` sí contempla reintentos debido a que sus problemas suelen ser de infraestructura transitoria.