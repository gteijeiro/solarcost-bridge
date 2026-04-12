from __future__ import annotations

import asyncio
import logging

from .api import start_api_server
from .client import LiveViewTotalsCollector
from .config import AppConfig
from .store import StateStore


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    config = AppConfig.from_args()
    configure_logging(config.log_level)

    logger = logging.getLogger("sa_totals_bridge")
    store = StateStore(config.db_path)
    api_server = start_api_server(config.bind_host, config.bind_port, store, logger.getChild("api"))
    logger.info("api listening on http://%s:%s", config.bind_host, config.bind_port)

    collector = LiveViewTotalsCollector(config, store, logger.getChild("collector"))
    try:
        asyncio.run(collector.run_forever())
    except KeyboardInterrupt:
        logger.info("shutting down")
    finally:
        api_server.shutdown()
        api_server.server_close()


if __name__ == "__main__":
    main()
