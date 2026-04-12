from __future__ import annotations

import copy
import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


def parse_float(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


class StateStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS snapshots (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._state = self._load_or_default()

    def _default_state(self) -> dict:
        now = utc_now()
        return {
            "service": {
                "connected": False,
                "base_url": None,
                "page_url": None,
                "ws_url": None,
                "topic": None,
                "root_id": None,
                "gateway_timezone": None,
                "last_login_at": None,
                "last_join_at": None,
                "last_message_at": None,
                "last_error": None,
                "reconnect_attempts": 0,
                "updated_at": now,
            },
            "daily": self._default_period_state("daily-data"),
            "monthly": self._default_period_state("weekly-data"),
        }

    @staticmethod
    def _default_period_state(event_name: str) -> dict:
        return {
            "event_name": event_name,
            "current_period_key": None,
            "periods": {},
            "updated_at": None,
        }

    def _load_or_default(self) -> dict:
        row = self._conn.execute("SELECT json FROM snapshots WHERE id = 1").fetchone()
        if not row:
            return self._default_state()
        try:
            data = json.loads(row[0])
        except json.JSONDecodeError:
            return self._default_state()

        default = self._default_state()
        default["service"].update(data.get("service", {}))
        default["daily"] = self._load_period_state(data.get("daily"), "daily-data")
        default["monthly"] = self._load_period_state(data.get("monthly"), "weekly-data")
        return default

    def _load_period_state(self, data: object, event_name: str) -> dict:
        default = self._default_period_state(event_name)
        if not isinstance(data, dict):
            return default

        if "periods" not in data:
            return self._migrate_legacy_period_state(data, event_name)

        default["current_period_key"] = data.get("current_period_key")
        default["updated_at"] = data.get("updated_at")

        raw_periods = data.get("periods")
        if isinstance(raw_periods, dict):
            raw_items = raw_periods.values()
        elif isinstance(raw_periods, list):
            raw_items = raw_periods
        else:
            raw_items = []

        periods: dict[str, dict] = {}
        for item in raw_items:
            normalized = self._normalize_loaded_period(item)
            if normalized is not None:
                existing = periods.get(normalized["period_key"])
                periods[normalized["period_key"]] = self._merge_periods(existing, normalized)

        default["periods"] = periods
        if default["current_period_key"] not in periods:
            default["current_period_key"] = self._latest_period_key(periods)
        return default

    def _normalize_loaded_period(self, period: object) -> dict | None:
        if not isinstance(period, dict):
            return None

        points = copy.deepcopy(period.get("points", [])) if isinstance(period.get("points"), list) else []
        rows = copy.deepcopy(period.get("rows", [])) if isinstance(period.get("rows"), list) else []
        window = copy.deepcopy(period.get("window")) if isinstance(period.get("window"), dict) else None

        period_key = period.get("period_key")
        if not isinstance(period_key, str) or not period_key:
            period_key = window.get("period_key") if isinstance(window, dict) else None

        if window is None and points:
            window = self._build_window(points)
            period_key = window["period_key"]
        elif isinstance(window, dict):
            canonical_key = self._canonical_period_key(window.get("started_at"), window.get("ended_at"))
            if canonical_key:
                period_key = canonical_key
            if period_key:
                window["period_key"] = period_key
            if "point_count" not in window:
                window["point_count"] = len(points)

        if not period_key:
            return None

        return {
            "period_key": period_key,
            "window": window,
            "rows": rows,
            "points": points,
            "updated_at": period.get("updated_at"),
        }

    def _migrate_legacy_period_state(self, data: dict, event_name: str) -> dict:
        migrated = self._default_period_state(event_name)
        chart = data.get("chart") if isinstance(data.get("chart"), dict) else {}
        points = copy.deepcopy(chart.get("points", [])) if isinstance(chart.get("points"), list) else []
        rows = copy.deepcopy(data.get("rows", [])) if isinstance(data.get("rows"), list) else []
        updated_at = data.get("updated_at")

        if not points and not rows:
            migrated["updated_at"] = updated_at
            return migrated

        if points:
            window = self._build_window(points)
            period_key = window["period_key"]
        else:
            period_key = f"legacy-{event_name}"
            window = {
                "period_key": period_key,
                "started_at": None,
                "ended_at": None,
                "point_count": 0,
            }

        migrated["current_period_key"] = period_key
        migrated["updated_at"] = updated_at
        migrated["periods"][period_key] = {
            "period_key": period_key,
            "window": window,
            "rows": rows,
            "points": points,
            "updated_at": updated_at,
        }
        return migrated

    def _save_locked(self) -> None:
        payload = json.dumps(self._state, ensure_ascii=True, separators=(",", ":"))
        now = utc_now()
        self._conn.execute(
            """
            INSERT INTO snapshots (id, json, updated_at)
            VALUES (1, ?, ?)
            ON CONFLICT(id) DO UPDATE SET json = excluded.json, updated_at = excluded.updated_at
            """,
            (payload, now),
        )
        self._conn.commit()

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._state)

    def current_period_key(self, period: str) -> str | None:
        if period not in {"daily", "monthly"}:
            raise ValueError(f"unknown period: {period}")
        with self._lock:
            value = self._state[period].get("current_period_key")
            return value if isinstance(value, str) else None

    def update_service(self, **values: object) -> None:
        with self._lock:
            self._state["service"].update(values)
            self._state["service"]["updated_at"] = utc_now()
            self._save_locked()

    def mark_connected(self, **values: object) -> None:
        values.setdefault("connected", True)
        values.setdefault("last_error", None)
        self.update_service(**values)

    def mark_disconnected(self, error: str | None = None, reconnect_attempts: int | None = None) -> None:
        values: dict[str, object] = {"connected": False}
        if error is not None:
            values["last_error"] = error
        if reconnect_attempts is not None:
            values["reconnect_attempts"] = reconnect_attempts
        self.update_service(**values)

    def touch_message(self) -> None:
        self.update_service(last_message_at=utc_now())

    def update_period(
        self,
        period: str,
        *,
        raw_data: list | None = None,
        rows: list[list[object]] | None = None,
        display_window: tuple[str, str] | None = None,
    ) -> str | None:
        if period not in {"daily", "monthly"}:
            raise ValueError(f"unknown period: {period}")
        if raw_data is None and rows is None:
            return self.current_period_key(period)

        with self._lock:
            period_state = self._state[period]
            period_key = period_state.get("current_period_key")

            if raw_data is not None:
                points = self._normalize_points(raw_data)
                if points or display_window is not None:
                    window = self._build_window(points, display_window=display_window)
                    period_key = window["period_key"]
                elif isinstance(period_key, str):
                    existing = period_state["periods"].get(period_key, {})
                    window = copy.deepcopy(existing.get("window")) if isinstance(existing, dict) else None
                    if not isinstance(window, dict):
                        window = {
                            "period_key": period_key,
                            "started_at": None,
                            "ended_at": None,
                            "point_count": 0,
                        }
                else:
                    window = None

                if isinstance(period_key, str):
                    entry = period_state["periods"].setdefault(period_key, self._empty_period_entry(period_key))
                    entry["window"] = window
                    entry["points"] = points
                    entry["updated_at"] = utc_now()
                    period_state["current_period_key"] = period_key

            if rows is not None:
                if display_window is not None:
                    period_key = self._build_window([], display_window=display_window)["period_key"]
                if not isinstance(period_key, str):
                    period_state["updated_at"] = utc_now()
                    self._save_locked()
                    current_key = period_state.get("current_period_key")
                    return current_key if isinstance(current_key, str) else None

                entry = period_state["periods"].setdefault(period_key, self._empty_period_entry(period_key))
                if display_window is not None and not isinstance(entry.get("window"), dict):
                    entry["window"] = self._build_window(entry.get("points", []), display_window=display_window)
                entry["rows"] = [self._normalize_row(row) for row in rows]
                entry["updated_at"] = utc_now()
                period_state["current_period_key"] = period_key

            period_state["updated_at"] = utc_now()
            self._save_locked()
            current_key = period_state.get("current_period_key")
            return current_key if isinstance(current_key, str) else None

    @staticmethod
    def _empty_period_entry(period_key: str) -> dict:
        return {
            "period_key": period_key,
            "window": None,
            "rows": [],
            "points": [],
            "updated_at": None,
        }

    def _normalize_points(self, raw_data: list) -> list[dict]:
        timestamps = raw_data[0] if len(raw_data) > 0 else []
        load = raw_data[1] if len(raw_data) > 1 else []
        grid = raw_data[2] if len(raw_data) > 2 else []
        solar = raw_data[3] if len(raw_data) > 3 else []
        timezone_name = self._state["service"].get("gateway_timezone") or "UTC"
        try:
            zone = ZoneInfo(timezone_name)
        except Exception:  # noqa: BLE001
            zone = ZoneInfo("UTC")

        points: list[dict] = []
        for index, timestamp in enumerate(timestamps):
            point = {
                "timestamp": timestamp,
                "iso": datetime.fromtimestamp(timestamp, tz=zone).isoformat(),
                "load_wh": self._series_value(load, index),
                "grid_wh": self._series_value(grid, index),
                "solar_pv_wh": self._series_value(solar, index),
            }
            for key in ("load_wh", "grid_wh", "solar_pv_wh"):
                value = point[key]
                point[key.replace("_wh", "_kwh")] = None if value is None else round(value / 1000.0, 6)
            points.append(point)

        return points

    @staticmethod
    def _build_window(points: list[dict], display_window: tuple[str, str] | None = None) -> dict:
        if display_window is not None:
            started_at, ended_at = display_window
        else:
            started_at = points[0]["iso"] if points else None
            ended_at = points[-1]["iso"] if points else None

        period_key = StateStore._canonical_period_key(started_at, ended_at)
        if not period_key:
            period_key = f"period-{utc_now().replace(':', '-')}"

        return {
            "period_key": period_key,
            "started_at": started_at,
            "ended_at": ended_at,
            "point_count": len(points),
        }

    @staticmethod
    def _canonical_period_key(started_at: object, ended_at: object) -> str | None:
        if not isinstance(started_at, str) or not isinstance(ended_at, str):
            return None

        def normalize(value: str) -> str:
            if "T" in value:
                return value.split("T", 1)[0]
            return value

        start = normalize(started_at)
        end = normalize(ended_at)
        if not start or not end:
            return None
        return f"{start}_{end}"

    @staticmethod
    def _merge_periods(existing: dict | None, incoming: dict) -> dict:
        if existing is None:
            return incoming

        merged = copy.deepcopy(existing)
        if not merged.get("window") and incoming.get("window"):
            merged["window"] = incoming["window"]
        if (not merged.get("rows")) and incoming.get("rows"):
            merged["rows"] = incoming["rows"]
        if (not merged.get("points")) and incoming.get("points"):
            merged["points"] = incoming["points"]
        if incoming.get("updated_at") and (
            not merged.get("updated_at") or incoming["updated_at"] > merged["updated_at"]
        ):
            merged["updated_at"] = incoming["updated_at"]
        return merged

    @staticmethod
    def _latest_period_key(periods: dict[str, dict]) -> str | None:
        if not periods:
            return None

        def sort_key(item: tuple[str, dict]) -> tuple[str, str]:
            _, period = item
            window = period.get("window") if isinstance(period, dict) else {}
            ended_at = window.get("ended_at") if isinstance(window, dict) else None
            updated_at = period.get("updated_at") if isinstance(period, dict) else None
            return (ended_at or "", updated_at or "")

        return max(periods.items(), key=sort_key)[0]

    @staticmethod
    def _series_value(values: list, index: int) -> float | None:
        if index >= len(values):
            return None
        value = values[index]
        return None if value is None else float(value)

    @staticmethod
    def _normalize_row(row: list[object]) -> dict:
        padded = list(row[:7]) + [None] * max(0, 7 - len(row))
        return {
            "label": padded[0],
            "load_kwh": parse_float(padded[1]),
            "solar_pv_kwh": parse_float(padded[2]),
            "battery_charged_kwh": parse_float(padded[3]),
            "battery_discharged_kwh": parse_float(padded[4]),
            "grid_used_kwh": parse_float(padded[5]),
            "grid_exported_kwh": parse_float(padded[6]),
        }
