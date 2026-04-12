from __future__ import annotations

import json
import logging
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import parse_qs, quote, urlsplit

from .store import StateStore


SWAGGER_UI_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
SWAGGER_UI_BUNDLE = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"


class JsonApiHandler(BaseHTTPRequestHandler):
    store: StateStore
    logger: logging.Logger

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        path = parts.path
        query = parse_qs(parts.query)
        snapshot = self.store.snapshot()

        if path == "/":
            self._json(
                {
                    "name": "solar-assistant-totals-bridge",
                    "status_endpoint": "/health",
                    "state_endpoint": "/state",
                    "daily_endpoint": "/totals/daily",
                    "daily_points_endpoint": "/totals/daily/points",
                    "monthly_endpoint": "/totals/monthly",
                    "monthly_points_endpoint": "/totals/monthly/points",
                    "openapi_endpoint": "/openapi.json",
                    "swagger_ui_endpoint": "/docs",
                }
            )
            return

        if path == "/openapi.json":
            self._json(build_openapi_spec())
            return

        if path == "/docs":
            self._html(build_swagger_ui_html())
            return

        if path == "/health":
            service = snapshot["service"]
            status = "ok" if service.get("connected") else "degraded"
            self._json(
                {
                    "status": status,
                    "connected": service.get("connected"),
                    "last_error": service.get("last_error"),
                    "last_message_at": service.get("last_message_at"),
                    "topic": service.get("topic"),
                }
            )
            return

        if path == "/state":
            self._json(
                {
                    "service": snapshot["service"],
                    "daily": build_totals_response(snapshot, "daily"),
                    "monthly": build_totals_response(snapshot, "monthly"),
                }
            )
            return

        if path == "/totals/daily":
            self._json(build_totals_response(snapshot, "daily"))
            return

        if path == "/totals/monthly":
            self._json(build_totals_response(snapshot, "monthly"))
            return

        if path == "/totals/daily/points":
            payload, status = build_points_response(snapshot, "daily", query)
            self._json(payload, status=status)
            return

        if path == "/totals/monthly/points":
            payload, status = build_points_response(snapshot, "monthly", query)
            self._json(payload, status=status)
            return

        self._json({"error": "not found", "path": path}, status=HTTPStatus.NOT_FOUND)

    def log_message(self, format: str, *args: object) -> None:
        self.logger.debug("api %s - %s", self.address_string(), format % args)

    def _json(self, payload: object, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = json.dumps(payload, ensure_ascii=True, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _html(self, html: str, status: HTTPStatus = HTTPStatus.OK) -> None:
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


def build_totals_response(snapshot: dict, period: str) -> dict:
    dataset = snapshot[period]
    periods = _sorted_periods(dataset)
    return {
        "event_name": dataset.get("event_name"),
        "current_period_key": dataset.get("current_period_key"),
        "period_count": len(periods),
        "windows": [_serialize_window(period, item) for item in periods],
        "rows": _flatten_rows(periods),
        "updated_at": dataset.get("updated_at"),
    }


def build_points_response(snapshot: dict, period: str, query: dict[str, list[str]]) -> tuple[dict, HTTPStatus]:
    dataset = snapshot[period]
    period_key = _first_query_value(query, "period_key")
    periods = _sorted_periods(dataset)

    if period_key is not None:
        periods = [item for item in periods if item.get("period_key") == period_key]
        if not periods:
            return {
                "error": "period not found",
                "period": period,
                "period_key": period_key,
            }, HTTPStatus.NOT_FOUND

    return {
        "event_name": dataset.get("event_name"),
        "current_period_key": dataset.get("current_period_key"),
        "period_count": len(periods),
        "windows": [_serialize_window(period, item) for item in periods],
        "points": _flatten_points(periods),
        "updated_at": dataset.get("updated_at"),
    }, HTTPStatus.OK


def _sorted_periods(dataset: dict) -> list[dict]:
    raw_periods = dataset.get("periods", {})
    if isinstance(raw_periods, dict):
        periods = list(raw_periods.values())
    elif isinstance(raw_periods, list):
        periods = list(raw_periods)
    else:
        periods = []

    def sort_key(item: dict) -> tuple[str, str]:
        window = item.get("window") if isinstance(item.get("window"), dict) else {}
        ended_at = window.get("ended_at") if isinstance(window, dict) else None
        updated_at = item.get("updated_at")
        return (ended_at or "", updated_at or "")

    return sorted(periods, key=sort_key, reverse=True)


def _serialize_window(period_name: str, period: dict) -> dict:
    window = period.get("window") if isinstance(period.get("window"), dict) else {}
    period_key = period.get("period_key")
    point_count = window.get("point_count")
    if point_count is None:
        point_count = len(period.get("points", []))

    encoded_period_key = quote(str(period_key or ""), safe="")
    return {
        "period_key": period_key,
        "started_at": window.get("started_at"),
        "ended_at": window.get("ended_at"),
        "point_count": point_count,
        "points_endpoint": f"/totals/{period_name}/points?period_key={encoded_period_key}",
    }


def _flatten_points(periods: list[dict]) -> list[dict]:
    seen: set[tuple[object, object]] = set()
    points: list[dict] = []

    for period in periods:
        raw_points = period.get("points", [])
        if not isinstance(raw_points, list):
            continue
        for point in raw_points:
            if not isinstance(point, dict):
                continue
            signature = (point.get("timestamp"), point.get("iso"))
            if signature in seen:
                continue
            seen.add(signature)
            points.append(point)

    return sorted(points, key=lambda item: (item.get("timestamp") or 0, item.get("iso") or ""))


def _flatten_rows(periods: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for period in periods:
        raw_rows = period.get("rows", [])
        if not isinstance(raw_rows, list):
            continue
        for row in raw_rows:
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _first_query_value(query: dict[str, list[str]], key: str) -> str | None:
    values = query.get(key)
    if not values:
        return None
    value = values[0].strip()
    return value or None


def build_openapi_spec() -> dict:
    return {
        "openapi": "3.0.3",
        "info": {
            "title": "Solar Assistant Totals Bridge API",
            "version": "0.2.0",
            "description": (
                "API local que publica el estado capturado desde SolarAssistant Totales "
                "a traves de Phoenix LiveView, con series temporales expuestas por separado."
            ),
        },
        "servers": [{"url": "/"}],
        "paths": {
            "/": {
                "get": {
                    "summary": "Describe los endpoints disponibles",
                    "responses": {
                        "200": {
                            "description": "Informacion basica de la API",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/RootResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/health": {
                "get": {
                    "summary": "Estado del collector",
                    "responses": {
                        "200": {
                            "description": "Estado del servicio",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/HealthResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/state": {
                "get": {
                    "summary": "Snapshot completo sin puntos embebidos",
                    "responses": {
                        "200": {
                            "description": "Estado completo del bridge",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/StateResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/totals/daily": {
                "get": {
                    "summary": "Filas diarias concatenadas",
                    "responses": {
                        "200": {
                            "description": "Filas diarias concatenadas a traves de todos los periodos cargados",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/TotalsCollectionResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/totals/monthly": {
                "get": {
                    "summary": "Filas mensuales concatenadas",
                    "responses": {
                        "200": {
                            "description": "Filas mensuales concatenadas a traves de todos los periodos cargados",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/TotalsCollectionResponse"}
                                }
                            },
                        }
                    },
                }
            },
            "/totals/daily/points": {
                "get": {
                    "summary": "Puntos diarios concatenados",
                    "parameters": [
                        {
                            "name": "period_key",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Filtra la respuesta a un periodo concreto.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Puntos diarios concatenados a traves de todos los periodos cargados",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PointsCollectionResponse"}
                                }
                            },
                        },
                        "404": {
                            "description": "Periodo no encontrado",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/totals/monthly/points": {
                "get": {
                    "summary": "Puntos mensuales concatenados",
                    "parameters": [
                        {
                            "name": "period_key",
                            "in": "query",
                            "required": False,
                            "schema": {"type": "string"},
                            "description": "Filtra la respuesta a un periodo concreto.",
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Puntos mensuales concatenados a traves de todos los periodos cargados",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/PointsCollectionResponse"}
                                }
                            },
                        },
                        "404": {
                            "description": "Periodo no encontrado",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        },
                    },
                }
            },
            "/openapi.json": {
                "get": {
                    "summary": "Especificacion OpenAPI",
                    "responses": {
                        "200": {
                            "description": "Documento OpenAPI de esta API",
                            "content": {
                                "application/json": {
                                    "schema": {"type": "object"}
                                }
                            },
                        }
                    },
                }
            },
            "/docs": {
                "get": {
                    "summary": "Swagger UI",
                    "responses": {
                        "200": {
                            "description": "Interfaz Swagger UI",
                            "content": {
                                "text/html": {
                                    "schema": {"type": "string"}
                                }
                            },
                        }
                    },
                }
            },
        },
        "components": {
            "schemas": {
                "RootResponse": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "status_endpoint": {"type": "string"},
                        "state_endpoint": {"type": "string"},
                        "daily_endpoint": {"type": "string"},
                        "daily_points_endpoint": {"type": "string"},
                        "monthly_endpoint": {"type": "string"},
                        "monthly_points_endpoint": {"type": "string"},
                        "openapi_endpoint": {"type": "string"},
                        "swagger_ui_endpoint": {"type": "string"},
                    },
                },
                "HealthResponse": {
                    "type": "object",
                    "properties": {
                        "status": {"type": "string", "example": "ok"},
                        "connected": {"type": "boolean"},
                        "last_error": {"type": "string", "nullable": True},
                        "last_message_at": {"type": "string", "nullable": True, "format": "date-time"},
                        "topic": {"type": "string", "nullable": True},
                    },
                },
                "PeriodWindow": {
                    "type": "object",
                    "properties": {
                        "period_key": {"type": "string", "nullable": True},
                        "started_at": {"type": "string", "nullable": True},
                        "ended_at": {"type": "string", "nullable": True},
                        "point_count": {"type": "integer"},
                        "points_endpoint": {"type": "string"},
                    },
                },
                "ChartPoint": {
                    "type": "object",
                    "properties": {
                        "timestamp": {"type": "integer"},
                        "iso": {"type": "string", "format": "date-time"},
                        "load_wh": {"type": "number", "nullable": True},
                        "grid_wh": {"type": "number", "nullable": True},
                        "solar_pv_wh": {"type": "number", "nullable": True},
                        "load_kwh": {"type": "number", "nullable": True},
                        "grid_kwh": {"type": "number", "nullable": True},
                        "solar_pv_kwh": {"type": "number", "nullable": True},
                    },
                },
                "TotalsRow": {
                    "type": "object",
                    "properties": {
                        "label": {"type": "string"},
                        "load_kwh": {"type": "number", "nullable": True},
                        "solar_pv_kwh": {"type": "number", "nullable": True},
                        "battery_charged_kwh": {"type": "number", "nullable": True},
                        "battery_discharged_kwh": {"type": "number", "nullable": True},
                        "grid_used_kwh": {"type": "number", "nullable": True},
                        "grid_exported_kwh": {"type": "number", "nullable": True},
                    },
                },
                "TotalsCollectionResponse": {
                    "type": "object",
                    "properties": {
                        "event_name": {"type": "string"},
                        "current_period_key": {"type": "string", "nullable": True},
                        "period_count": {"type": "integer"},
                        "windows": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PeriodWindow"},
                        },
                        "rows": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/TotalsRow"},
                        },
                        "updated_at": {"type": "string", "nullable": True, "format": "date-time"},
                    },
                },
                "PointsCollectionResponse": {
                    "type": "object",
                    "properties": {
                        "event_name": {"type": "string"},
                        "current_period_key": {"type": "string", "nullable": True},
                        "period_count": {"type": "integer"},
                        "windows": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/PeriodWindow"},
                        },
                        "points": {
                            "type": "array",
                            "items": {"$ref": "#/components/schemas/ChartPoint"},
                        },
                        "updated_at": {"type": "string", "nullable": True, "format": "date-time"},
                    },
                },
                "StateResponse": {
                    "type": "object",
                    "properties": {
                        "service": {"type": "object"},
                        "daily": {"$ref": "#/components/schemas/TotalsCollectionResponse"},
                        "monthly": {"$ref": "#/components/schemas/TotalsCollectionResponse"},
                    },
                },
                "ErrorResponse": {
                    "type": "object",
                    "properties": {
                        "error": {"type": "string"},
                        "period": {"type": "string", "nullable": True},
                        "period_key": {"type": "string", "nullable": True},
                        "path": {"type": "string", "nullable": True},
                    },
                },
            }
        },
    }


def build_swagger_ui_html() -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Solar Assistant Totals Bridge - Swagger UI</title>
  <link rel="stylesheet" href="{SWAGGER_UI_CSS}">
  <style>
    body {{
      margin: 0;
      background: #fafafa;
    }}
    .topbar {{
      display: none;
    }}
  </style>
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="{SWAGGER_UI_BUNDLE}"></script>
  <script>
    window.onload = function () {{
      window.ui = SwaggerUIBundle({{
        url: "/openapi.json",
        dom_id: "#swagger-ui",
        deepLinking: true,
        displayRequestDuration: true,
        presets: [
          SwaggerUIBundle.presets.apis
        ],
        layout: "BaseLayout"
      }});
    }};
  </script>
</body>
</html>"""


def start_api_server(host: str, port: int, store: StateStore, logger: logging.Logger) -> ThreadingHTTPServer:
    handler = type(
        "BoundJsonApiHandler",
        (JsonApiHandler,),
        {
            "store": store,
            "logger": logger,
        },
    )
    server = ThreadingHTTPServer((host, port), handler)
    thread = Thread(target=server.serve_forever, name="api-server", daemon=True)
    thread.start()
    return server
