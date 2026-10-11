"""Управление клиентами AmneziaWG на сервере Amnezia VPN (Docker) по SSH.

Скрипт выполняется внутри контейнера (docker exec) и делает то же, что приложение
Amnezia при добавлении пользователя: добавляет [Peer] в wg0.conf, применяет его через
wg syncconf и записывает клиента в clientsTable, чтобы он был виден в разделе
«Пользователи» приложения AmneziaVPN.

Сам Amnezia приватные ключи клиентов на сервере не хранит, поэтому бот сохраняет
созданные им конфиги в <awg_dir>/bot-clients/<имя>.conf — оттуда их берёт /get.
"""

import base64
import json
import shlex
import struct
import zlib

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from config import AmneziaServer
from vpn_common import COMMON_EXIT_MESSAGES, BaseManager, ClientConfig, VpnError

REMOTE_SCRIPT = r"""
set -euo pipefail
ACTION="${1:-}"
AWG_DIR="${2:-/opt/amnezia/awg}"
CLIENT_NAME="${3:-}"
ENDPOINT_HOST="${4:-}"
CLIENT_DNS="${5:-1.1.1.1, 1.0.0.1}"

SERVER_CONF=""
for f in "$AWG_DIR/wg0.conf" "$AWG_DIR/awg0.conf"; do
    if [[ -f $f ]]; then SERVER_CONF=$f; break; fi
done
[[ -n $SERVER_CONF ]] || { echo "no wg0.conf/awg0.conf in $AWG_DIR" >&2; exit 21; }
NIC=$(basename "$SERVER_CONF" .conf)
if command -v awg >/dev/null 2>&1; then WG=awg; WGQ=awg-quick; else WG=wg; WGQ=wg-quick; fi

CLIENTS_TABLE="$AWG_DIR/clientsTable"
CLIENTS_DIR="$AWG_DIR/bot-clients"
CLIENT_CONF="$CLIENTS_DIR/${CLIENT_NAME}.conf"

check_name() {
    if ! [[ $CLIENT_NAME =~ ^[a-zA-Z0-9_-]+$ && ${#CLIENT_NAME} -lt 16 ]]; then
        echo "invalid client name" >&2
        exit 10
    fi
}

# Значение параметра из секции [Interface] конфига сервера
iface_get() {
    awk -v key="$1" '
        /^[ \t]*\[/ { in_iface = ($0 ~ /^[ \t]*\[Interface\]/); next }
        in_iface && index($0, "=") {
            k = $0; sub(/=.*/, "", k); gsub(/[ \t]/, "", k)
            if (tolower(k) == tolower(key)) {
                v = $0; sub(/^[^=]*=[ \t]*/, "", v); sub(/[ \t\r]+$/, "", v)
                print v; exit
            }
        }' "$SERVER_CONF"
}

case "$ACTION" in
list)
    cat "$CLIENTS_TABLE" 2>/dev/null || echo "[]"
    ;;
get)
    check_name
    [[ -f $CLIENT_CONF ]] || { echo "not found: $CLIENT_CONF" >&2; exit 13; }
    echo "FILE=${CLIENT_CONF}"
    cat "$CLIENT_CONF"
    ;;
add)
    check_name
    if [[ -f $CLIENT_CONF ]] \
        || grep -qE "\"clientName\"[[:space:]]*:[[:space:]]*\"${CLIENT_NAME}\"" "$CLIENTS_TABLE" 2>/dev/null; then
        echo "client already exists" >&2
        exit 11
    fi

    SERVER_ADDR=$(iface_get Address)
    SERVER_IP="${SERVER_ADDR%%,*}"
    SERVER_IP="${SERVER_IP%%/*}"
    LISTEN_PORT=$(iface_get ListenPort)
    [[ -n $SERVER_IP && -n $LISTEN_PORT ]] || { echo "no Address/ListenPort in $SERVER_CONF" >&2; exit 22; }

    BASE_IP4="${SERVER_IP%.*}"
    SERVER_DOT="${SERVER_IP##*.}"
    BASE_RE="${BASE_IP4//./\\.}"
    DOT_IP=""
    for i in $(seq 1 254); do
        [[ $i == "$SERVER_DOT" ]] && continue
        if ! grep -qE "(^|[^0-9.])${BASE_RE}\.${i}/32" "$SERVER_CONF"; then
            DOT_IP=$i
            break
        fi
    done
    [[ -n $DOT_IP ]] || { echo "no free IP in subnet" >&2; exit 12; }
    CLIENT_IP="${BASE_IP4}.${DOT_IP}"

    umask 077
    mkdir -p "$CLIENTS_DIR"
    CLIENT_PRIV_KEY=$($WG genkey)
    CLIENT_PUB_KEY=$(echo "$CLIENT_PRIV_KEY" | $WG pubkey)
    if [[ -s $AWG_DIR/wireguard_psk.key ]]; then
        PSK=$(tr -d '[:space:]' <"$AWG_DIR/wireguard_psk.key")
    else
        PSK=$($WG genpsk)
    fi
    if [[ -s $AWG_DIR/wireguard_server_public_key.key ]]; then
        SERVER_PUB_KEY=$(tr -d '[:space:]' <"$AWG_DIR/wireguard_server_public_key.key")
    else
        SERVER_PUB_KEY=$(iface_get PrivateKey | $WG pubkey)
    fi

    ENDPOINT_IP="$ENDPOINT_HOST"
    if [[ $ENDPOINT_IP == *:* && $ENDPOINT_IP != *"["* ]]; then
        ENDPOINT_IP="[${ENDPOINT_IP}]"
    fi

    # Параметры обфускации AmneziaWG должны совпадать с сервером
    OBF=""
    for key in Jc Jmin Jmax S1 S2 S3 S4 H1 H2 H3 H4 I1 I2 I3 I4 I5; do
        value=$(iface_get "$key")
        if [[ -n $value ]]; then OBF+="${key} = ${value}"$'\n'; fi
    done

    cat >"$CLIENT_CONF" <<EOF
[Interface]
Address = ${CLIENT_IP}/32
DNS = ${CLIENT_DNS}
PrivateKey = ${CLIENT_PRIV_KEY}
${OBF}
[Peer]
PublicKey = ${SERVER_PUB_KEY}
PresharedKey = ${PSK}
AllowedIPs = 0.0.0.0/0, ::/0
Endpoint = ${ENDPOINT_IP}:${LISTEN_PORT}
PersistentKeepalive = 25
EOF

    printf '\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = %s/32\n' \
        "$CLIENT_PUB_KEY" "$PSK" "$CLIENT_IP" >>"$SERVER_CONF"

    $WG syncconf "$NIC" <($WGQ strip "$SERVER_CONF")

    # Добавляем клиента в clientsTable (JSON-массив), чтобы его видело приложение Amnezia
    ENTRY="{\"clientId\": \"${CLIENT_PUB_KEY}\", \"userData\": {\"clientName\": \"${CLIENT_NAME}\", \"creationDate\": \"$(date '+%a %b %d %H:%M:%S %Y')\"}}"
    TABLE=$(cat "$CLIENTS_TABLE" 2>/dev/null || true)
    if [[ $TABLE == *"]"* ]]; then TABLE="${TABLE%]*}"; else TABLE="["; fi
    TABLE="${TABLE%"${TABLE##*[![:space:]]}"}"
    if [[ $TABLE == *"[" ]]; then SEP=""; else SEP=","; fi
    printf '%s%s\n    %s\n]\n' "$TABLE" "$SEP" "$ENTRY" >"$CLIENTS_TABLE.tmp"
    mv "$CLIENTS_TABLE.tmp" "$CLIENTS_TABLE"

    echo "FILE=${CLIENT_CONF}"
    cat "$CLIENT_CONF"
    ;;
*)
    echo "unknown action: $ACTION" >&2
    exit 2
    ;;
esac
"""

EXIT_MESSAGES = {
    **COMMON_EXIT_MESSAGES,
    12: "В подсети AmneziaWG не осталось свободных IP-адресов.",
    21: "В контейнере нет wg0.conf / awg0.conf — проверьте AMNEZIA_CONTAINER и AMNEZIA_AWG_DIR.",
    22: "В конфиге сервера AmneziaWG нет Address или ListenPort.",
}

# Параметры обфускации, которые переносятся в ключ vpn://
OBF_KEYS = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4",
            "I1", "I2", "I3", "I4", "I5")


def _parse_conf(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("[") and line.endswith("]"):
            current = sections.setdefault(line[1:-1].strip().lower(), {})
        elif current is not None and "=" in line:
            key, _, value = line.partition("=")
            current[key.strip()] = value.strip()
    return sections


def _split_list(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


class AmneziaManager(BaseManager):
    exit_messages = EXIT_MESSAGES

    def __init__(self, server: AmneziaServer) -> None:
        super().__init__(server.key, server.title, server.ssh)
        self._container = server.container
        self._awg_dir = server.awg_dir
        self._endpoint = server.endpoint
        self._dns = server.dns

    def _command(self, action: str, name: str) -> str:
        args = " ".join(
            shlex.quote(a) for a in (action, self._awg_dir, name, self._endpoint, self._dns)
        )
        return (
            f"docker exec {shlex.quote(self._container)} "
            f"bash -c {shlex.quote(REMOTE_SCRIPT)} awg-bot {args}"
        )

    async def list_clients(self) -> list[str]:
        output = await self._call("list")
        try:
            table = json.loads(output or "[]")
        except json.JSONDecodeError as e:
            raise VpnError(f"Не удалось разобрать clientsTable: {e}") from e
        names = []
        for entry in table if isinstance(table, list) else []:
            if not isinstance(entry, dict):
                continue
            user_data = entry.get("userData") or {}
            names.append(user_data.get("clientName") or str(entry.get("clientId", "?"))[:12])
        return names

    def vpn_key(self, client: ClientConfig) -> str:
        """Ключ vpn:// для импорта в приложение AmneziaVPN (протокол AmneziaWG)."""
        conf = _parse_conf(client.config)
        iface, peer = conf.get("interface", {}), conf.get("peer", {})
        try:
            priv_key = iface["PrivateKey"]
            host, _, port = peer["Endpoint"].rpartition(":")
            pub_key = base64.b64encode(
                X25519PrivateKey.from_private_bytes(base64.b64decode(priv_key))
                .public_key()
                .public_bytes_raw()
            ).decode()
        except (KeyError, ValueError) as e:
            raise VpnError(f"Не удалось разобрать конфиг клиента: {e}") from e
        host = host.strip("[]")
        dns = _split_list(iface.get("DNS", "")) + ["1.1.1.1", "1.0.0.1"]
        obf = {k: iface[k] for k in OBF_KEYS if k in iface}

        last_config = {
            **obf,
            "allowed_ips": _split_list(peer.get("AllowedIPs", "0.0.0.0/0, ::/0")),
            "clientId": pub_key,
            "client_ip": client.ipv4 or "",
            "client_priv_key": priv_key,
            "client_pub_key": pub_key,
            "config": client.config,
            "hostName": host,
            "mtu": iface.get("MTU", "1280"),
            "persistent_keep_alive": peer.get("PersistentKeepalive", "25"),
            "port": int(port),
            "psk_key": peer.get("PresharedKey", ""),
            "server_pub_key": peer.get("PublicKey", ""),
        }
        data = {
            "containers": [{
                "awg": {
                    **obf,
                    "last_config": json.dumps(last_config, indent=4),
                    "port": port,
                    "transport_proto": "udp",
                },
                "container": self._container,
            }],
            "defaultContainer": self._container,
            "description": self.title,
            "dns1": dns[0],
            "dns2": dns[1],
            "hostName": host,
        }

        # Формат Amnezia: qCompress (4 байта длины big-endian + zlib) → base64url без «=»
        raw = json.dumps(data, indent=4, ensure_ascii=False).encode()
        packed = struct.pack(">I", len(raw)) + zlib.compress(raw, 8)
        return "vpn://" + base64.urlsafe_b64encode(packed).decode().rstrip("=")
