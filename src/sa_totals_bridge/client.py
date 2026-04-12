from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from http.cookiejar import CookieJar
from typing import Any

import websockets

from .config import AppConfig
from .html import TotalsPageContext, extract_sign_in_csrf, parse_totals_page
from .store import StateStore, utc_now


@dataclass(slots=True)
class AuthenticatedSession:
    config: AppConfig
    cookie_jar: CookieJar
    opener: urllib.request.OpenerDirector

    @classmethod
    def create(cls, config: AppConfig) -> "AuthenticatedSession":
        cookie_jar = CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))
        return cls(config=config, cookie_jar=cookie_jar, opener=opener)

    def fetch_text(self, path: str, data: dict[str, Any] | None = None) -> str:
        url = path if path.startswith("http") else f"{self.config.base_url}{path}"
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers={"User-Agent": self.config.user_agent})
        with self.opener.open(request, timeout=self.config.connect_timeout) as response:
            return response.read().decode("utf-8")

    def cookie_header(self) -> str:
        return "; ".join(f"{cookie.name}={cookie.value}" for cookie in self.cookie_jar)

    def ws_url(self, csrf_token: str) -> str:
        parsed = urllib.parse.urlsplit(self.config.base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        query = urllib.parse.urlencode({"_csrf_token": csrf_token, "vsn": "2.0.0"})
        return urllib.parse.urlunsplit((scheme, parsed.netloc, "/live/websocket", query, ""))

    def login_and_open_totals(self) -> TotalsPageContext:
        sign_in_html = self.fetch_text("/sign_in")
        csrf_token = extract_sign_in_csrf(sign_in_html)
        self.fetch_text("/sign_in", {"_csrf_token": csrf_token, "password": self.config.password})
        totals_html = self.fetch_text("/totals")
        context = parse_totals_page(totals_html, f"{self.config.base_url}/totals")
        return context


class LiveViewTotalsCollector:
    def __init__(self, config: AppConfig, store: StateStore, logger: logging.Logger) -> None:
        self.config = config
        self.store = store
        self.logger = logger
        self._message_ref = 1
        self._join_ref = "1"
        self._topic = ""
        self._pending_rows: dict[str, list[list[Any]] | None] = {
            "daily": None,
            "monthly": None,
        }

    async def run_forever(self) -> None:
        reconnect_attempts = 0
        while True:
            try:
                session = AuthenticatedSession.create(self.config)
                page = session.login_and_open_totals()
                self.store.update_service(
                    base_url=self.config.base_url,
                    page_url=page.page_url,
                    gateway_timezone=page.gateway_timezone,
                    last_login_at=utc_now(),
                    reconnect_attempts=reconnect_attempts,
                )
                await self._run_session(session, page)
                reconnect_attempts = 0
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                reconnect_attempts += 1
                self.store.mark_disconnected(
                    error=str(exc),
                    reconnect_attempts=reconnect_attempts,
                )
                self.logger.exception("collector loop failed")
                await asyncio.sleep(self.config.reconnect_delay)

    async def _run_session(self, session: AuthenticatedSession, page: TotalsPageContext) -> None:
        self._message_ref = 1
        self._join_ref = "1"
        self._topic = f"lv:{page.root_id}"
        self._pending_rows = {"daily": None, "monthly": None}
        ws_url = session.ws_url(page.csrf_token)

        self.store.mark_connected(
            ws_url=ws_url,
            topic=self._topic,
            root_id=page.root_id,
            last_join_at=utc_now(),
        )
        self.logger.info("connecting to %s", ws_url)

        async with websockets.connect(
            ws_url,
            additional_headers={
                "Cookie": session.cookie_header(),
                "Origin": self.config.base_url,
                "User-Agent": self.config.user_agent,
            },
            open_timeout=self.config.connect_timeout,
            ping_interval=None,
            ping_timeout=None,
            max_size=None,
        ) as websocket:
            heartbeat_task = asyncio.create_task(self._heartbeat_loop(websocket))
            receiver_task = asyncio.create_task(self._receive_loop(websocket))
            try:
                await self._send_join(websocket, page)
                await self._wait_for_initial_periods(receiver_task)
                await self._backfill_history(websocket, receiver_task)
                await receiver_task
            finally:
                heartbeat_task.cancel()
                receiver_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await heartbeat_task
                with contextlib.suppress(asyncio.CancelledError):
                    await receiver_task
                self.store.mark_disconnected()

    async def _send_join(self, websocket: websockets.ClientConnection, page: TotalsPageContext) -> None:
        join_payload = {
            "url": page.page_url,
            "params": {
                "_csrf_token": page.csrf_token,
                "_track_static": page.track_static,
                "_mounts": 0,
                "_live_referer": None,
            },
            "session": page.session,
            "static": page.static,
            "flash": None,
        }
        join_message = [self._join_ref, self._join_ref, self._topic, "phx_join", join_payload]
        await websocket.send(json.dumps(join_message))

    async def _heartbeat_loop(self, websocket: websockets.ClientConnection) -> None:
        while True:
            await asyncio.sleep(self.config.heartbeat_interval)
            ref = self._next_ref()
            heartbeat = [None, ref, "phoenix", "heartbeat", {}]
            await websocket.send(json.dumps(heartbeat))

    async def _receive_loop(self, websocket: websockets.ClientConnection) -> None:
        while True:
            raw_message = await websocket.recv()
            self.store.touch_message()
            await self._handle_message(raw_message)

    async def _handle_message(self, raw_message: str | bytes) -> None:
        if isinstance(raw_message, bytes):
            self.logger.debug("ignoring binary websocket frame with %d bytes", len(raw_message))
            return

        join_ref, ref, topic, event, payload = json.loads(raw_message)
        self.logger.debug("recv topic=%s event=%s ref=%s join_ref=%s", topic, event, ref, join_ref)

        if topic == "phoenix":
            return
        if topic != self._topic:
            self.logger.debug("ignoring topic %s", topic)
            return

        if event == "phx_reply":
            status = payload.get("status")
            response = payload.get("response", {})
            if status != "ok":
                raise RuntimeError(f"liveview reply status was {status!r}: {response!r}")
            if "rendered" in response:
                self.logger.info("joined liveview topic %s", self._topic)
            diff = response.get("diff")
            if isinstance(diff, dict):
                self._apply_diff(diff)
            return

        if event == "diff" and isinstance(payload, dict):
            self._apply_diff(payload)
            return

        if event in {"redirect", "live_patch", "live_redirect"}:
            self.logger.info("received %s event: %s", event, payload)
            return

        if event in {"phx_error", "phx_close"}:
            raise RuntimeError(f"liveview channel closed with event {event}")

    def _apply_diff(self, diff: dict[str, Any]) -> None:
        windows = extract_period_windows(diff)
        chart_updates: dict[str, list | None] = {
            "daily": None,
            "monthly": None,
        }

        for event_name, event_payload in diff.get("e", []):
            if event_name == "daily-data":
                chart_updates["daily"] = event_payload.get("data", [])
            elif event_name == "weekly-data":
                chart_updates["monthly"] = event_payload.get("data", [])

        tables = extract_totals_tables(diff)
        row_updates = {
            "daily": tables.daily_rows,
            "monthly": tables.monthly_rows,
        }
        display_windows = {
            "daily": windows.daily,
            "monthly": windows.monthly,
        }

        for period in ("daily", "monthly"):
            raw_data = chart_updates[period]
            if raw_data is not None:
                self.store.update_period(period, raw_data=raw_data, display_window=display_windows[period])
                pending_rows = self._pending_rows.get(period)
                if pending_rows is not None:
                    self.store.update_period(period, rows=pending_rows, display_window=display_windows[period])
                    self._pending_rows[period] = None

            rows = row_updates[period]
            if rows is not None:
                if display_windows[period] is not None or self.store.current_period_key(period):
                    self.store.update_period(period, rows=rows, display_window=display_windows[period])
                else:
                    self._pending_rows[period] = rows

    async def _backfill_history(
        self,
        websocket: websockets.ClientConnection,
        receiver_task: asyncio.Task[None],
    ) -> None:
        await self._backfill_period(
            websocket,
            receiver_task,
            period="daily",
            previous_event="prev-daily",
            next_event="next-daily",
            steps=self.config.daily_history_periods,
        )
        await self._backfill_period(
            websocket,
            receiver_task,
            period="monthly",
            previous_event="prev-monthly",
            next_event="next-monthly",
            steps=self.config.monthly_history_periods,
        )

    async def _backfill_period(
        self,
        websocket: websockets.ClientConnection,
        receiver_task: asyncio.Task[None],
        *,
        period: str,
        previous_event: str,
        next_event: str,
        steps: int,
    ) -> None:
        if steps <= 0:
            return

        original_key = await self._wait_for_current_period(period, receiver_task)
        if original_key is None:
            return

        current_key = original_key
        visited = {original_key}
        successful_steps = 0

        for _ in range(steps):
            await self._push_click(websocket, previous_event)
            try:
                next_key = await self._wait_for_period_change(period, current_key, receiver_task)
            except TimeoutError:
                self.logger.info("stopping %s backfill after %d period(s)", period, successful_steps)
                break

            if next_key in visited:
                self.logger.info("stopping %s backfill at repeated period %s", period, next_key)
                break

            visited.add(next_key)
            current_key = next_key
            successful_steps += 1

        while successful_steps > 0:
            await self._push_click(websocket, next_event)
            try:
                current_key = await self._wait_for_period_change(period, current_key, receiver_task)
            except TimeoutError:
                self.logger.warning("could not return %s timeline to the latest period", period)
                return
            successful_steps -= 1

        if current_key != original_key:
            self.logger.warning(
                "%s timeline ended at %s instead of expected %s",
                period,
                current_key,
                original_key,
            )

    async def _push_click(self, websocket: websockets.ClientConnection, event_name: str) -> None:
        payload = {
            "type": "click",
            "event": event_name,
            "value": "",
        }
        message = [self._join_ref, self._next_ref(), self._topic, "event", payload]
        await websocket.send(json.dumps(message))

    async def _wait_for_initial_periods(self, receiver_task: asyncio.Task[None]) -> None:
        deadline = asyncio.get_running_loop().time() + self.config.connect_timeout
        while True:
            await self._raise_if_receiver_finished(receiver_task)
            if self.store.current_period_key("daily") and self.store.current_period_key("monthly"):
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError("timed out waiting for initial daily/monthly periods")
            await asyncio.sleep(0.1)

    async def _wait_for_current_period(
        self,
        period: str,
        receiver_task: asyncio.Task[None],
    ) -> str | None:
        deadline = asyncio.get_running_loop().time() + self.config.connect_timeout
        while True:
            await self._raise_if_receiver_finished(receiver_task)
            period_key = self.store.current_period_key(period)
            if period_key is not None:
                return period_key
            if asyncio.get_running_loop().time() >= deadline:
                return None
            await asyncio.sleep(0.1)

    async def _wait_for_period_change(
        self,
        period: str,
        previous_key: str,
        receiver_task: asyncio.Task[None],
    ) -> str:
        deadline = asyncio.get_running_loop().time() + self.config.connect_timeout
        while True:
            await self._raise_if_receiver_finished(receiver_task)
            current_key = self.store.current_period_key(period)
            if current_key is not None and current_key != previous_key:
                return current_key
            if asyncio.get_running_loop().time() >= deadline:
                raise TimeoutError(f"timed out waiting for {period} period change from {previous_key}")
            await asyncio.sleep(0.1)

    @staticmethod
    async def _raise_if_receiver_finished(receiver_task: asyncio.Task[None]) -> None:
        if receiver_task.done():
            await receiver_task

    def _next_ref(self) -> str:
        self._message_ref += 1
        return str(self._message_ref)


@dataclass(slots=True)
class TotalsTables:
    daily_rows: list[list[Any]] | None = None
    monthly_rows: list[list[Any]] | None = None


@dataclass(slots=True)
class PeriodWindows:
    daily: tuple[str, str] | None = None
    monthly: tuple[str, str] | None = None


DATE_RANGE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
MONTH_RANGE_RE = re.compile(r"^\d{4}-\d{2}$")


def extract_period_windows(diff: Any) -> PeriodWindows:
    result = PeriodWindows()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            left = node.get("0")
            right = node.get("1")
            separators = node.get("s")
            if isinstance(left, str) and isinstance(right, str) and isinstance(separators, list):
                joined = " ".join(str(item) for item in separators)
                if "to" in joined:
                    if DATE_RANGE_RE.fullmatch(left) and DATE_RANGE_RE.fullmatch(right):
                        result.daily = (left, right)
                    elif MONTH_RANGE_RE.fullmatch(left) and MONTH_RANGE_RE.fullmatch(right):
                        result.monthly = (left, right)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(diff)
    return result


def extract_totals_tables(diff: Any) -> TotalsTables:
    candidates: list[list[list[Any]]] = []
    seen: set[tuple[Any, ...]] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            rows = node.get("d")
            if looks_like_totals_rows(rows):
                signature = (
                    len(rows),
                    tuple(rows[0]),
                    tuple(rows[-1]),
                )
                if signature not in seen:
                    seen.add(signature)
                    candidates.append(rows)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(diff)

    result = TotalsTables()
    for rows in candidates:
        if len(rows) >= 20 and result.daily_rows is None:
            result.daily_rows = rows
        elif 8 <= len(rows) <= 15 and result.monthly_rows is None:
            result.monthly_rows = rows
    return result


def looks_like_totals_rows(rows: Any) -> bool:
    if not isinstance(rows, list) or not rows:
        return False
    for row in rows:
        if not isinstance(row, list) or len(row) != 7:
            return False
        if not isinstance(row[0], str):
            return False
    return True
