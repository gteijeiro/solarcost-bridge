from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path


def default_db_path() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "solar_assistant_totals.sqlite3"


@dataclass(slots=True)
class AppConfig:
    base_url: str
    password: str
    bind_host: str
    bind_port: int
    db_path: Path
    log_level: str
    reconnect_delay: float
    heartbeat_interval: float
    connect_timeout: float
    daily_history_periods: int
    monthly_history_periods: int
    user_agent: str

    @classmethod
    def from_args(cls) -> "AppConfig":
        parser = argparse.ArgumentParser(
            description="Collect SolarAssistant Totals LiveView data and publish it over HTTP."
        )
        parser.add_argument("--base-url", default=os.getenv("SA_BASE_URL"))
        parser.add_argument("--password", default=os.getenv("SA_PASSWORD"))
        parser.add_argument("--bind-host", default=os.getenv("SA_BIND_HOST", "0.0.0.0"))
        parser.add_argument("--bind-port", type=int, default=int(os.getenv("SA_BIND_PORT", "8765")))
        parser.add_argument(
            "--db-path",
            default=os.getenv("SA_DB_PATH", str(default_db_path())),
        )
        parser.add_argument("--log-level", default=os.getenv("SA_LOG_LEVEL", "INFO"))
        parser.add_argument(
            "--reconnect-delay",
            type=float,
            default=float(os.getenv("SA_RECONNECT_DELAY", "5")),
        )
        parser.add_argument(
            "--heartbeat-interval",
            type=float,
            default=float(os.getenv("SA_HEARTBEAT_INTERVAL", "30")),
        )
        parser.add_argument(
            "--connect-timeout",
            type=float,
            default=float(os.getenv("SA_CONNECT_TIMEOUT", "10")),
        )
        parser.add_argument(
            "--daily-history-periods",
            type=int,
            default=int(os.getenv("SA_DAILY_HISTORY_PERIODS", "12")),
        )
        parser.add_argument(
            "--monthly-history-periods",
            type=int,
            default=int(os.getenv("SA_MONTHLY_HISTORY_PERIODS", "5")),
        )
        parser.add_argument(
            "--user-agent",
            default=os.getenv("SA_USER_AGENT", "sa-totals-bridge/0.1"),
        )
        args = parser.parse_args()

        if not args.base_url:
            parser.error("missing --base-url or SA_BASE_URL")
        if not args.password:
            parser.error("missing --password or SA_PASSWORD")

        return cls(
            base_url=args.base_url.rstrip("/"),
            password=args.password,
            bind_host=args.bind_host,
            bind_port=args.bind_port,
            db_path=Path(args.db_path).expanduser(),
            log_level=args.log_level.upper(),
            reconnect_delay=args.reconnect_delay,
            heartbeat_interval=args.heartbeat_interval,
            connect_timeout=args.connect_timeout,
            daily_history_periods=max(args.daily_history_periods, 0),
            monthly_history_periods=max(args.monthly_history_periods, 0),
            user_agent=args.user_agent,
        )
