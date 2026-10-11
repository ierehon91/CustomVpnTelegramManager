"""Общая часть менеджеров VPN-серверов: SSH, коды ошибок, разбор ответа скрипта."""

import asyncio
import re
from dataclasses import dataclass

import asyncssh

from config import SshSettings

# Те же ограничения, что и в wireguard-install.sh
NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,15}$")

# Коды выхода удалённых скриптов (общие для всех серверов)
COMMON_EXIT_MESSAGES = {
    10: "Недопустимое имя: только латиница, цифры, «_» и «-», не длиннее 15 символов.",
    11: "Клиент с таким именем уже существует.",
    12: "В подсети не осталось свободных IP-адресов.",
    13: "Конфиг этого клиента не найден на сервере.",
}


class VpnError(Exception):
    pass


@dataclass(frozen=True)
class ClientConfig:
    name: str
    ipv4: str | None
    ipv6: str | None
    server_path: str
    config: str


def parse_client(name: str, output: str) -> ClientConfig:
    first, _, config = output.partition("\n")
    if not first.startswith("FILE="):
        raise VpnError(f"Неожиданный ответ сервера: {first[:200]}")

    ipv4 = ipv6 = None
    match = re.search(r"^Address\s*=\s*(.+)$", config, re.MULTILINE)
    if match:
        for part in match.group(1).split(","):
            addr = part.strip().split("/")[0]
            if ":" in addr:
                ipv6 = addr
            elif addr:
                ipv4 = addr

    return ClientConfig(
        name=name,
        ipv4=ipv4,
        ipv6=ipv6,
        server_path=first.removeprefix("FILE=").strip(),
        config=config,
    )


class BaseManager:
    """Выполняет действия list/get/add удалённым bash-скриптом по SSH."""

    exit_messages: dict[int, str] = COMMON_EXIT_MESSAGES

    def __init__(self, key: str, title: str, ssh: SshSettings) -> None:
        self.key = key
        self.title = title
        self._ssh = ssh
        # Не даём двум запросам одновременно выбрать один и тот же свободный IP
        self._add_lock = asyncio.Lock()

    def _command(self, action: str, name: str) -> str:
        raise NotImplementedError

    def _connect_kwargs(self) -> dict:
        s = self._ssh
        kwargs: dict = {
            "host": s.host,
            "port": s.port,
            "username": s.user,
            "known_hosts": s.known_hosts,  # None — без проверки ключа хоста
            "connect_timeout": 15,
        }
        if s.password:
            kwargs["password"] = s.password
        if s.key_path:
            kwargs["client_keys"] = [s.key_path]
            if s.key_passphrase:
                kwargs["passphrase"] = s.key_passphrase
        return kwargs

    async def _call(self, action: str, name: str = "") -> str:
        command = self._command(action, name)
        if self._ssh.user != "root":
            command = "sudo -n " + command

        try:
            async with asyncssh.connect(**self._connect_kwargs()) as conn:
                result = await conn.run(command, check=False, timeout=60)
        except (OSError, asyncssh.Error, asyncio.TimeoutError) as e:
            raise VpnError(f"Ошибка SSH ({self._ssh.host}): {e}") from e

        if result.exit_status != 0:
            message = self.exit_messages.get(result.exit_status)
            if message is None:
                stderr = str(result.stderr or "").strip()[-500:]
                message = f"Команда на сервере завершилась с кодом {result.exit_status}: {stderr}"
            raise VpnError(message)
        return str(result.stdout or "")

    async def add_client(self, name: str) -> ClientConfig:
        if not NAME_RE.fullmatch(name):
            raise VpnError(self.exit_messages[10])
        async with self._add_lock:
            return parse_client(name, await self._call("add", name))

    async def get_client(self, name: str) -> ClientConfig:
        if not NAME_RE.fullmatch(name):
            raise VpnError(self.exit_messages[10])
        return parse_client(name, await self._call("get", name))

    async def list_clients(self) -> list[str]:
        output = await self._call("list")
        return [line.strip() for line in output.splitlines() if line.strip()]
