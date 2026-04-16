from __future__ import annotations

import unittest
from pathlib import Path

from sa_totals_bridge.config import AppConfig
from sa_totals_bridge.install import (
    BridgeInstallConfig,
    build_env_file,
    build_service_file,
    permission_help,
    validate_install_config,
)
from sa_totals_bridge.uninstall import (
    BridgeUninstallConfig,
    permission_help as uninstall_permission_help,
    validate_uninstall_config,
)


class BridgeConfigTests(unittest.TestCase):
    def test_from_args_accepts_explicit_argv(self) -> None:
        config = AppConfig.from_args(
            [
                "--base-url",
                "http://127.0.0.1",
                "--password",
                "secret",
                "--refresh-interval",
                "15",
            ]
        )

        self.assertEqual(config.base_url, "http://127.0.0.1")
        self.assertEqual(config.password, "secret")
        self.assertEqual(config.bind_port, 8765)
        self.assertEqual(config.refresh_interval, 15.0)


class BridgeInstallerTests(unittest.TestCase):
    def test_build_env_file_contains_expected_values(self) -> None:
        config = BridgeInstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            base_url="http://127.0.0.1",
            password="secret.123",
            bind_host="0.0.0.0",
            bind_port=8765,
            log_level="INFO",
            reconnect_delay=5.0,
            heartbeat_interval=30.0,
            refresh_interval=10.0,
            connect_timeout=10.0,
            daily_history_periods=6,
            monthly_history_periods=3,
            user_agent="sa-totals-bridge/0.1",
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            service_user="solar-assistant",
            service_group="solar-assistant",
            enable_now=True,
        )

        content = build_env_file(config)

        self.assertIn('SA_BASE_URL="http://127.0.0.1"', content)
        self.assertIn('SA_PASSWORD="secret.123"', content)
        self.assertIn('SA_DAILY_HISTORY_PERIODS="6"', content)
        self.assertIn('SA_MONTHLY_HISTORY_PERIODS="3"', content)
        self.assertIn('SA_REFRESH_INTERVAL="10.0"', content)

    def test_build_service_file_uses_current_module_execution(self) -> None:
        config = BridgeInstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            base_url="http://127.0.0.1",
            password="secret",
            bind_host="0.0.0.0",
            bind_port=8765,
            log_level="INFO",
            reconnect_delay=5.0,
            heartbeat_interval=30.0,
            refresh_interval=10.0,
            connect_timeout=10.0,
            daily_history_periods=6,
            monthly_history_periods=3,
            user_agent="sa-totals-bridge/0.1",
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            service_user="solar-assistant",
            service_group="solar-assistant",
            enable_now=True,
        )

        content = build_service_file(config, Path("/opt/solar-assistant/bridge/.venv/bin/python"))

        self.assertIn("EnvironmentFile=/opt/solar-assistant/bridge/bridge.env", content)
        self.assertIn("ExecStart=/opt/solar-assistant/bridge/.venv/bin/python -m sa_totals_bridge run", content)
        self.assertIn("User=solar-assistant", content)

    def test_validate_install_config_requires_root_for_system_service(self) -> None:
        config = BridgeInstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            base_url="http://127.0.0.1",
            password="secret",
            bind_host="0.0.0.0",
            bind_port=8765,
            log_level="INFO",
            reconnect_delay=5.0,
            heartbeat_interval=30.0,
            refresh_interval=10.0,
            connect_timeout=10.0,
            daily_history_periods=6,
            monthly_history_periods=3,
            user_agent="sa-totals-bridge/0.1",
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            service_user="solar-assistant",
            service_group="solar-assistant",
            enable_now=True,
        )

        with self.assertRaises(RuntimeError) as raised:
            validate_install_config(config)

        self.assertIn("sudo", str(raised.exception))

    def test_permission_help_mentions_full_binary_path(self) -> None:
        config = BridgeInstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            base_url="http://127.0.0.1",
            password="secret",
            bind_host="0.0.0.0",
            bind_port=8765,
            log_level="INFO",
            reconnect_delay=5.0,
            heartbeat_interval=30.0,
            refresh_interval=10.0,
            connect_timeout=10.0,
            daily_history_periods=6,
            monthly_history_periods=3,
            user_agent="sa-totals-bridge/0.1",
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            service_user="solar-assistant",
            service_group="solar-assistant",
            enable_now=True,
        )

        help_text = permission_help(config)

        self.assertIn("sudo", help_text)
        self.assertIn("command -v sa-totals-bridge", help_text)


class BridgeUninstallTests(unittest.TestCase):
    def test_validate_uninstall_config_requires_root_for_system_service(self) -> None:
        config = BridgeUninstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            remove_service=True,
            remove_env_file=False,
            remove_db_file=False,
            remove_runtime_dir=False,
            uninstall_package=False,
        )

        with self.assertRaises(RuntimeError) as raised:
            validate_uninstall_config(config)

        self.assertIn("sudo", str(raised.exception))

    def test_uninstall_permission_help_mentions_subcommand(self) -> None:
        config = BridgeUninstallConfig(
            runtime_dir=Path("/opt/solar-assistant/bridge"),
            env_path=Path("/opt/solar-assistant/bridge/bridge.env"),
            db_path=Path("/opt/solar-assistant/bridge/data/solar_assistant_totals.sqlite3"),
            service_mode="system",
            service_name="sa-totals-bridge.service",
            service_path=Path("/etc/systemd/system/sa-totals-bridge.service"),
            remove_service=True,
            remove_env_file=False,
            remove_db_file=False,
            remove_runtime_dir=False,
            uninstall_package=False,
        )

        help_text = uninstall_permission_help(config)

        self.assertIn("uninstall", help_text)
        self.assertIn("command -v sa-totals-bridge", help_text)


if __name__ == "__main__":
    unittest.main()
