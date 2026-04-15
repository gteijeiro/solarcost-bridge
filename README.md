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
pip install .
```

Bootstrap automatico desde el repo:

```bash
./init.sh
```

Tambien puedes instalarlo directamente desde GitHub:

```bash
pipx install "git+https://github.com/gteijeiro/solar-assistant-costs-bridge.git"
```

Y luego ejecutar el asistente:

```bash
sa-totals-bridge init
```

## Despliegue rapido en Raspberry Pi

1. Clona el repo en la Pi.
2. Entra a la carpeta del proyecto.
3. Ejecuta `./init.sh`.
4. Responde el asistente interactivo.
5. Si elegiste `system` o `user`, el script deja generado el service y puede habilitarlo automaticamente.

Ejemplo:

```bash
git clone https://github.com/gteijeiro/solar-assistant-costs-bridge.git
cd solar-assistant-costs-bridge
./init.sh
```

Para ver logs del servicio:

```bash
sudo journalctl -u sa-totals-bridge.service -f
```

## Ejecucion

```bash
export SA_BASE_URL="http://SOLAR_ASSISTANT_HOST_O_IP"
export SA_PASSWORD="TU_PASSWORD_DE_SOLAR_ASSISTANT"
sa-totals-bridge
```

Tambien puedes usar el subcomando explicito:

```bash
sa-totals-bridge run
```

La API queda por defecto en:

- `http://127.0.0.1:8765`
- `http://<tu-ip-local>:8765`

## Como funciona la actualizacion

- El bridge abre una sesion autenticada contra Solar Assistant.
- Luego abre un websocket de Phoenix LiveView hacia la solapa `Totales`.
- La API HTTP del bridge no abre un websocket por cada request.
- Cada request a la API solo devuelve el ultimo snapshot guardado en memoria y SQLite.
- `SA_HEARTBEAT_INTERVAL` mantiene viva la conexion websocket.
- `SA_REFRESH_INTERVAL` fuerza una resincronizacion periodica del periodo actual porque la vista `Totales` no siempre emite cambios nuevos por si sola.

En otras palabras:

- `heartbeat` no trae datos nuevos, solo evita que la conexion muera.
- `refresh` vuelve a abrir la sesion del collector y refresca los valores actuales.

Por defecto el bridge:

- manda heartbeat cada `30` segundos,
- fuerza resincronizacion cada `10` segundos,
- hace backfill historico una vez al arrancar el collector,
- despues mantiene el historial ya guardado y solo refresca el periodo actual.

Si quieres menos carga en la Pi o en Solar Assistant:

```bash
export SA_DAILY_HISTORY_PERIODS="6"
export SA_MONTHLY_HISTORY_PERIODS="3"
export SA_REFRESH_INTERVAL="20"
```

Si quieres desactivar la resincronizacion forzada y dejar solo el websocket:

```bash
export SA_REFRESH_INTERVAL="0"
```

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
- `SA_REFRESH_INTERVAL`
- `SA_CONNECT_TIMEOUT`
- `SA_DAILY_HISTORY_PERIODS`
- `SA_MONTHLY_HISTORY_PERIODS`

## Diagnostico rapido

Para saber si el bridge esta trayendo datos recientes, revisa:

- `GET /health`
- `GET /state`

Campos utiles:

- `service.connected`
- `service.last_login_at`
- `service.last_join_at`
- `service.last_message_at`
- `service.last_error`
- `daily.updated_at`
- `monthly.updated_at`

Si `connected=true` pero `last_message_at` o `daily.updated_at` quedan viejos durante mucho tiempo, normalmente significa que Solar Assistant no esta empujando diffs nuevos y dependes del `SA_REFRESH_INTERVAL`.

## Recomendacion de seguridad

- No guardes `SA_PASSWORD` en archivos versionados.
- No subas la base `data/solar_assistant_totals.sqlite3` a GitHub.
- Si usas `.env`, mantenlo fuera del repositorio.

## Notas

- El collector hace una busqueda hacia atras con `prev-daily` y `prev-monthly`, y luego vuelve al periodo actual con `next-daily` y `next-monthly`.
- Por defecto intenta traer `12` periodos diarios anteriores y `5` periodos mensuales anteriores.
- Swagger UI esta en `/docs` y carga assets desde CDN.

## Build y publicacion

Build local:

```bash
python -m pip install --upgrade build
python -m build
```

El repo incluye workflows de GitHub Actions para:

- CI en `push` y `pull_request`,
- publicacion manual a TestPyPI con `workflow_dispatch`,
- publicacion a PyPI al crear un tag `v*`.
