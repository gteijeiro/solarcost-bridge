from __future__ import annotations

import argparse
import getpass
import os
import pwd
import grp
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class BridgeInstallConfig:
    runtime_dir: Path
    env_path: Path
    db_path: Path
    base_url: str
    password: str
    bind_host: str
    bind_port: int
    log_level: str
    reconnect_delay: float
    heartbeat_interval: float
    refresh_interval: float
    connect_timeout: float
    daily_history_periods: int
    monthly_history_periods: int
    user_agent: str
    service_mode: str
    service_name: str
    service_path: Path | None
    service_user: str | None
    service_group: str | None
    enable_now: bool


def run_init(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="sa-totals-bridge init",
        description="Asistente interactivo para configurar e instalar el bridge.",
    )
    parser.parse_args(argv)

    install_config = prompt_install_config()
    write_runtime_files(install_config)
    if install_config.service_mode != "none" and install_config.enable_now:
        enable_service(install_config)
    print_summary(install_config)
    return 0


def prompt_install_config() -> BridgeInstallConfig:
    current_user = pwd.getpwuid(os.getuid()).pw_name
    current_group = grp.getgrgid(os.getgid()).gr_name
    runtime_dir = Path(
        prompt_text(
            "Directorio de trabajo del bridge",
            str(Path.cwd()),
        )
    ).expanduser()
    default_env_path = runtime_dir / "bridge.env"
    default_db_path = runtime_dir / "data" / "solar_assistant_totals.sqlite3"
    base_url = prompt_text("URL base de Solar Assistant", "http://192.168.0.79").rstrip("/")
    password = prompt_secret("Password de Solar Assistant")
    bind_host = prompt_text("Host para publicar la API", "0.0.0.0")
    bind_port = prompt_int("Puerto para publicar la API", 8765)
    log_level = prompt_text("Nivel de log", "INFO").upper()
    reconnect_delay = prompt_float("Segundos de reintento", 5.0)
    heartbeat_interval = prompt_float("Segundos de heartbeat", 30.0)
    refresh_interval = prompt_float("Segundos entre resincronizaciones forzadas", 10.0)
    connect_timeout = prompt_float("Timeout de conexion", 10.0)
    daily_history_periods = prompt_int("Cantidad de periodos diarios historicos", 12)
    monthly_history_periods = prompt_int("Cantidad de periodos mensuales historicos", 5)
    user_agent = prompt_text("User-Agent HTTP", "sa-totals-bridge/0.1")
    env_path = Path(prompt_text("Archivo de entorno", str(default_env_path))).expanduser()
    db_path = Path(prompt_text("Ruta de la base SQLite", str(default_db_path))).expanduser()

    service_mode = prompt_choice(
        "Modo de servicio",
        choices=("system", "user", "none"),
        default="system" if is_root() else "user",
    )
    service_name = prompt_text("Nombre del servicio", "sa-totals-bridge.service")
    service_user: str | None = None
    service_group: str | None = None
    service_path: Path | None = None
    enable_now = False

    if service_mode == "system":
        service_path = Path(prompt_text("Ruta del unit file", f"/etc/systemd/system/{service_name}")).expanduser()
        service_user = prompt_text("Usuario del servicio", current_user)
        service_group = prompt_text("Grupo del servicio", current_group)
        enable_now = prompt_yes_no("Habilitar e iniciar el servicio ahora", True)
    elif service_mode == "user":
        service_path = Path(
            prompt_text(
                "Ruta del unit file",
                str(Path.home() / ".config" / "systemd" / "user" / service_name),
            )
        ).expanduser()
        enable_now = prompt_yes_no("Habilitar e iniciar el servicio de usuario ahora", True)

    return BridgeInstallConfig(
        runtime_dir=runtime_dir,
        env_path=env_path,
        db_path=db_path,
        base_url=base_url,
        password=password,
        bind_host=bind_host,
        bind_port=bind_port,
        log_level=log_level,
        reconnect_delay=reconnect_delay,
        heartbeat_interval=heartbeat_interval,
        refresh_interval=refresh_interval,
        connect_timeout=connect_timeout,
        daily_history_periods=max(daily_history_periods, 0),
        monthly_history_periods=max(monthly_history_periods, 0),
        user_agent=user_agent,
        service_mode=service_mode,
        service_name=service_name,
        service_path=service_path,
        service_user=service_user,
        service_group=service_group,
        enable_now=enable_now,
    )


def write_runtime_files(install_config: BridgeInstallConfig) -> None:
    install_config.runtime_dir.mkdir(parents=True, exist_ok=True)
    install_config.db_path.parent.mkdir(parents=True, exist_ok=True)
    install_config.env_path.parent.mkdir(parents=True, exist_ok=True)

    write_text_file(
        install_config.env_path,
        build_env_file(install_config),
    )
    if install_config.service_mode != "none" and install_config.service_path is not None:
        install_config.service_path.parent.mkdir(parents=True, exist_ok=True)
        write_text_file(
            install_config.service_path,
            build_service_file(install_config, Path(sys.executable)),
        )


def build_env_file(install_config: BridgeInstallConfig) -> str:
    lines = [
        "# Solar Assistant Costs Bridge",
        env_line("SA_BASE_URL", install_config.base_url),
        env_line("SA_PASSWORD", install_config.password),
        env_line("SA_BIND_HOST", install_config.bind_host),
        env_line("SA_BIND_PORT", str(install_config.bind_port)),
        env_line("SA_DB_PATH", str(install_config.db_path)),
        env_line("SA_LOG_LEVEL", install_config.log_level),
        env_line("SA_RECONNECT_DELAY", str(install_config.reconnect_delay)),
        env_line("SA_HEARTBEAT_INTERVAL", str(install_config.heartbeat_interval)),
        env_line("SA_REFRESH_INTERVAL", str(install_config.refresh_interval)),
        env_line("SA_CONNECT_TIMEOUT", str(install_config.connect_timeout)),
        env_line("SA_DAILY_HISTORY_PERIODS", str(install_config.daily_history_periods)),
        env_line("SA_MONTHLY_HISTORY_PERIODS", str(install_config.monthly_history_periods)),
        env_line("SA_USER_AGENT", install_config.user_agent),
        "",
    ]
    return "\n".join(lines)


def build_service_file(install_config: BridgeInstallConfig, python_executable: Path) -> str:
    wanted_by = "multi-user.target" if install_config.service_mode == "system" else "default.target"
    user_lines: list[str] = []
    if install_config.service_mode == "system" and install_config.service_user and install_config.service_group:
        user_lines = [
            f"User={install_config.service_user}",
            f"Group={install_config.service_group}",
        ]

    service_lines = [
        "[Unit]",
        "Description=Solar Assistant Costs Bridge",
        "Wants=network-online.target",
        "After=network-online.target",
        "",
        "[Service]",
        "Type=simple",
        *user_lines,
        f"WorkingDirectory={install_config.runtime_dir}",
        f"EnvironmentFile={install_config.env_path}",
        f"ExecStart={python_executable} -m sa_totals_bridge run",
        "Restart=always",
        "RestartSec=5",
        "",
        "[Install]",
        f"WantedBy={wanted_by}",
        "",
    ]
    return "\n".join(service_lines)


def enable_service(install_config: BridgeInstallConfig) -> None:
    if install_config.service_mode == "none":
        return
    if install_config.service_path is None:
        raise RuntimeError("No se encontro la ruta del servicio para habilitar.")
    if shutil.which("systemctl") is None:
        raise RuntimeError("No se encontro systemctl. El servicio fue generado, pero debes habilitarlo manualmente.")

    try:
        if install_config.service_mode == "system":
            if not is_root():
                raise RuntimeError("Para instalar un servicio de sistema debes ejecutar el init con sudo o elegir modo user.")
            subprocess.run(["systemctl", "daemon-reload"], check=True)
            subprocess.run(["systemctl", "enable", "--now", install_config.service_name], check=True)
            return

        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", install_config.service_name], check=True)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"No se pudo habilitar el servicio automaticamente: {exc}") from exc


def print_summary(install_config: BridgeInstallConfig) -> None:
    print("")
    print("Bridge configurado.")
    print(f"- Directorio: {install_config.runtime_dir}")
    print(f"- Env file: {install_config.env_path}")
    print(f"- Base de datos: {install_config.db_path}")
    if install_config.service_mode != "none" and install_config.service_path is not None:
        print(f"- Servicio: {install_config.service_path}")
        if install_config.service_mode == "user":
            print("- Si quieres que el servicio de usuario arranque al boot, revisa `loginctl enable-linger`.")


def env_line(key: str, value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'{key}="{escaped}"'


def write_text_file(path: Path, content: str) -> None:
    if path.exists() and not prompt_yes_no(f"{path} ya existe. Sobrescribir", False):
        raise RuntimeError(f"Operacion cancelada. No se sobrescribio {path}.")
    path.write_text(content, encoding="utf-8")


def prompt_text(label: str, default: str | None = None) -> str:
    while True:
        suffix = f" [{default}]" if default else ""
        value = input(f"{label}{suffix}: ").strip()
        if value:
            return value
        if default is not None:
            return default
        print("Este valor es obligatorio.")


def prompt_secret(label: str) -> str:
    while True:
        value = getpass.getpass(f"{label}: ").strip()
        if value:
            return value
        print("Este valor es obligatorio.")


def prompt_int(label: str, default: int) -> int:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return int(raw)
        except ValueError:
            print("Debes ingresar un numero entero.")


def prompt_float(label: str, default: float) -> float:
    while True:
        raw = input(f"{label} [{default}]: ").strip()
        if not raw:
            return default
        try:
            return float(raw)
        except ValueError:
            print("Debes ingresar un numero valido.")


def prompt_yes_no(label: str, default: bool) -> bool:
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = input(f"{label} [{suffix}]: ").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes", "s", "si", "sí"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Responde si o no.")


def prompt_choice(label: str, *, choices: tuple[str, ...], default: str) -> str:
    choice_text = "/".join(choices)
    while True:
        raw = input(f"{label} ({choice_text}) [{default}]: ").strip().lower()
        if not raw:
            return default
        if raw in choices:
            return raw
        print(f"Debes elegir una de estas opciones: {choice_text}.")


def is_root() -> bool:
    geteuid = getattr(os, "geteuid", None)
    return bool(geteuid and geteuid() == 0)
