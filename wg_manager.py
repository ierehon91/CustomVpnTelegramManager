"""Управление клиентами WireGuard на удалённом сервере по SSH.

Логика повторяет функцию newClient() из angristan/wireguard-install, поэтому
созданные ботом клиенты видны в ./wireguard-install.sh (List / Revoke) и наоборот.
"""

import shlex

from config import WireGuardServer
from vpn_common import COMMON_EXIT_MESSAGES, BaseManager

REMOTE_SCRIPT = r"""
set -euo pipefail
ACTION="${1:-}"
CONF_DIR="${2:-/root}"
CLIENT_NAME="${3:-}"

PARAMS=/etc/wireguard/params
[[ -f $PARAMS ]] || { echo "$PARAMS not found" >&2; exit 20; }
source "$PARAMS"
SERVER_CONF="/etc/wireguard/${SERVER_WG_NIC}.conf"
CLIENT_CONF="${CONF_DIR}/${SERVER_WG_NIC}-client-${CLIENT_NAME}.conf"

check_name() {
    if ! [[ $CLIENT_NAME =~ ^[a-zA-Z0-9_-]+$ && ${#CLIENT_NAME} -lt 16 ]]; then
        echo "invalid client name" >&2
        exit 10
    fi
}

case "$ACTION" in
list)
    grep -E '^### Client ' "$SERVER_CONF" | cut -d ' ' -f 3 || true
    ;;
get)
    check_name
    [[ -f $CLIENT_CONF ]] || { echo "not found: $CLIENT_CONF" >&2; exit 13; }
    echo "FILE=${CLIENT_CONF}"
    cat "$CLIENT_CONF"
    ;;
add)
    check_name
    if grep -qE "^### Client ${CLIENT_NAME}\$" "$SERVER_CONF"; then
        echo "client already exists" >&2
        exit 11
    fi

    BASE_IP4="${SERVER_WG_IPV4%.*}"
    BASE_IP6="${SERVER_WG_IPV6%%::*}"
    DOT_IP=""
    for i in $(seq 2 254); do
        if ! grep -qF "${BASE_IP4}.${i}/32" "$SERVER_CONF" \
            && ! grep -qF "${BASE_IP6}::${i}/128" "$SERVER_CONF"; then
            DOT_IP=$i
            break
        fi
    done
    [[ -n $DOT_IP ]] || { echo "no free IP in subnet" >&2; exit 12; }
    CLIENT_WG_IPV4="${BASE_IP4}.${DOT_IP}"
    CLIENT_WG_IPV6="${BASE_IP6}::${DOT_IP}"

    umask 077
    CLIENT_PRIV_KEY=$(wg genkey)
    CLIENT_PUB_KEY=$(echo "$CLIENT_PRIV_KEY" | wg pubkey)
    CLIENT_PRE_SHARED_KEY=$(wg genpsk)

    ENDPOINT_IP="$SERVER_PUB_IP"
    if [[ $ENDPOINT_IP == *:* && $ENDPOINT_IP != *"["* ]]; then
        ENDPOINT_IP="[${ENDPOINT_IP}]"
    fi

    cat >"$CLIENT_CONF" <<EOF
[Interface]
PrivateKey = ${CLIENT_PRIV_KEY}
Address = ${CLIENT_WG_IPV4}/32,${CLIENT_WG_IPV6}/128
DNS = ${CLIENT_DNS_1:-1.1.1.1},${CLIENT_DNS_2:-1.0.0.1}

[Peer]
PublicKey = ${SERVER_PUB_KEY}
PresharedKey = ${CLIENT_PRE_SHARED_KEY}
Endpoint = ${ENDPOINT_IP}:${SERVER_PORT}
AllowedIPs = ${ALLOWED_IPS:-0.0.0.0/0,::/0}
EOF

    printf '\n### Client %s\n[Peer]\nPublicKey = %s\nPresharedKey = %s\nAllowedIPs = %s/32,%s/128\n' \
        "$CLIENT_NAME" "$CLIENT_PUB_KEY" "$CLIENT_PRE_SHARED_KEY" \
        "$CLIENT_WG_IPV4" "$CLIENT_WG_IPV6" >>"$SERVER_CONF"

    wg syncconf "$SERVER_WG_NIC" <(wg-quick strip "$SERVER_WG_NIC")

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
    12: "В подсети WireGuard не осталось свободных IP-адресов.",
    20: "На сервере нет /etc/wireguard/params — WireGuard установлен не через wireguard-install.sh?",
}


class WireGuardManager(BaseManager):
    exit_messages = EXIT_MESSAGES

    def __init__(self, server: WireGuardServer) -> None:
        super().__init__(server.key, server.title, server.ssh)
        self._clients_dir = server.clients_dir

    def _command(self, action: str, name: str) -> str:
        args = " ".join(shlex.quote(a) for a in (action, self._clients_dir, name))
        return f"bash -c {shlex.quote(REMOTE_SCRIPT)} wg-bot {args}"
