from __future__ import annotations

import asyncio
import logging
import sys

from .api import start_api_server
from .client import LiveViewTotalsCollector
from .config import AppConfig
from .install import run_init
from .store import StateStore
from .uninstall import run_uninstall


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def main() -> None:
    argv = sys.argv[1:]
    if argv and argv[0] == "init":
        raise SystemExit(run_init(argv[1:]))
    if argv and argv[0] == "uninstall":
        raise SystemExit(run_uninstall(argv[1:]))
    if argv and argv[0] == "run":
        argv = argv[1:]

    config = AppConfig.from_args(argv)
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
