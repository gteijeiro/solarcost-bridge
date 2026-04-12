# Solar Assistant Costs Bridge

Bridge en Python para:

- autenticarse contra SolarAssistant,
- conectarse al websocket de Phoenix LiveView de la solapa `Totales`,
- mantener un estado actualizado y persistido,
- publicar ese estado por HTTP para que otra aplicacion lo consulte.

## Estructura

- `src/sa_totals_bridge`: codigo fuente
- `requirements.txt`: dependencias
- `data/`: base SQLite local generada en ejecucion

## Instalacion

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Ejecucion

```bash
source .venv/bin/activate
export SA_BASE_URL="http://SOLAR_ASSISTANT_HOST_O_IP"
export SA_PASSWORD="TU_PASSWORD_DE_SOLAR_ASSISTANT"
PYTHONPATH=src python -m sa_totals_bridge
```

La API queda por defecto en:

- `http://127.0.0.1:8765`
- `http://<tu-ip-local>:8765`

## Endpoints

- `GET /health`
- `GET /state`
- `GET /totals/daily`
- `GET /totals/daily/points`
- `GET /totals/monthly`
- `GET /totals/monthly/points`
- `GET /openapi.json`
- `GET /docs`

Los endpoints de `points` devuelven una sola lista concatenada de todos los periodos cargados. Si necesitas un periodo puntual, puedes usar `?period_key=...`.

## Variables disponibles

- `SA_BASE_URL`
- `SA_PASSWORD`
- `SA_BIND_HOST`
- `SA_BIND_PORT`
- `SA_DB_PATH`
- `SA_LOG_LEVEL`
- `SA_RECONNECT_DELAY`
- `SA_HEARTBEAT_INTERVAL`
- `SA_CONNECT_TIMEOUT`
- `SA_DAILY_HISTORY_PERIODS`
- `SA_MONTHLY_HISTORY_PERIODS`

## Recomendacion de seguridad

- No guardes `SA_PASSWORD` en archivos versionados.
- No subas la base `data/solar_assistant_totals.sqlite3` a GitHub.
- Si usas `.env`, mantenlo fuera del repositorio.

## Notas

- El collector hace una busqueda hacia atras con `prev-daily` y `prev-monthly`, y luego vuelve al periodo actual con `next-daily` y `next-monthly`.
- Por defecto intenta traer `12` periodos diarios anteriores y `5` periodos mensuales anteriores.
- Swagger UI esta en `/docs` y carga assets desde CDN.
