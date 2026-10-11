import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Переменная окружения {name} не задана (см. .env.example)")
    return value


def _optional(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name, "").strip()
    return value or default


@dataclass(frozen=True)
class SshSettings:
    host: str
    port: int
    user: str
    password: str | None
    key_path: str | None
    key_passphrase: str | None
    known_hosts: str | None


@dataclass(frozen=True)
class WireGuardServer:
    """Сервер с WireGuard, установленным через angristan/wireguard-install."""

    key: str
    title: str
    ssh: SshSettings
    # Каталог на сервере, куда кладутся конфиги клиентов (как у wireguard-install.sh)
    clients_dir: str


@dataclass(frozen=True)
class AmneziaServer:
    """Сервер Amnezia VPN с протоколом AmneziaWG в Docker-контейнере."""

    key: str
    title: str
    ssh: SshSettings
    container: str
    # Каталог с конфигами AmneziaWG внутри контейнера
    awg_dir: str
    # Адрес сервера, который попадёт в Endpoint клиентского конфига
    endpoint: str
    dns: str


@dataclass(frozen=True)
class Settings:
    bot_token: str
    allowed_user_ids: frozenset[int]
    servers: tuple[WireGuardServer | AmneziaServer, ...]


def _load_ssh(prefix: str) -> SshSettings | None:
    host = _optional(f"{prefix}SSH_HOST")
    if not host:
        return None
    password = _optional(f"{prefix}SSH_PASSWORD")
    key_path = _optional(f"{prefix}SSH_KEY_PATH")
    if not password and not key_path:
        raise RuntimeError(f"Укажите {prefix}SSH_PASSWORD или {prefix}SSH_KEY_PATH")
    return SshSettings(
        host=host,
        port=int(_optional(f"{prefix}SSH_PORT", "22")),
        user=_optional(f"{prefix}SSH_USER", "root"),
        password=password,
        key_path=key_path,
        key_passphrase=_optional(f"{prefix}SSH_KEY_PASSPHRASE"),
        known_hosts=_optional(f"{prefix}SSH_KNOWN_HOSTS"),
    )


def load_settings() -> Settings:
    allowed = frozenset(
        int(x) for x in _required("ALLOWED_USER_IDS").replace(" ", "").split(",") if x
    )

    servers: list[WireGuardServer | AmneziaServer] = []

    wg_ssh = _load_ssh("")
    if wg_ssh:
        servers.append(WireGuardServer(
            key="wg",
            title=_optional("WG_SERVER_NAME", "WireGuard"),
            ssh=wg_ssh,
            clients_dir=_optional("WG_CLIENTS_DIR", "/root").rstrip("/") or "/",
        ))

    amnezia_ssh = _load_ssh("AMNEZIA_")
    if amnezia_ssh:
        servers.append(AmneziaServer(
            key="amnezia",
            title=_optional("AMNEZIA_SERVER_NAME", "Amnezia"),
            ssh=amnezia_ssh,
            container=_optional("AMNEZIA_CONTAINER", "amnezia-awg"),
            awg_dir=_optional("AMNEZIA_AWG_DIR", "/opt/amnezia/awg").rstrip("/") or "/",
            endpoint=_optional("AMNEZIA_ENDPOINT", amnezia_ssh.host),
            dns=_optional("AMNEZIA_DNS", "1.1.1.1, 1.0.0.1"),
        ))

    if not servers:
        raise RuntimeError("Не задан ни один сервер: укажите SSH_HOST и/или AMNEZIA_SSH_HOST")

    return Settings(
        bot_token=_required("BOT_TOKEN"),
        allowed_user_ids=allowed,
        servers=tuple(servers),
    )
