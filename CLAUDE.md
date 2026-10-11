# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

Telegram bot (aiogram 3) that creates VPN clients on remote servers over SSH (asyncssh). Two server types are supported, each optional (at least one must be configured): a WireGuard server installed via [angristan/wireguard-install](https://github.com/angristan/wireguard-install) (`SSH_*` vars), and an Amnezia VPN server with the AmneziaWG protocol in Docker (`AMNEZIA_*` vars). User-facing text, comments and error messages are in Russian — keep it that way.

## Commands

```bash
python -m venv .venv
.venv\Scripts\activate            # Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # fill in BOT_TOKEN, ALLOWED_USER_IDS, SSH_* and/or AMNEZIA_*
python bot.py
```

There are no tests, linter config or build step. Running the bot requires a real Telegram token and at least one SSH-reachable server (WireGuard or Amnezia). Python 3.10+ (`X | None` annotations).

## Deployment

The README's «Установка и запуск как службы» section is the user-facing install guide: production runs as a systemd unit `wg-bot.service` from `/opt/wg-bot` (venv in `.venv`, `.env` and SSH key in `.ssh/` there) under an unprivileged system user `wgbot`, with `Restart=always` and hardening (`ProtectSystem=strict`, `ProtectHome=true`, `PrivateTmp=true`, `NoNewPrivileges=true`). Keep it in sync with the code:

- The unit forbids local disk writes, which works because the bot keeps everything in memory (configs/QR via `BufferedInputFile`, FSM in the `Dispatcher()` default in-memory storage). If a change makes the bot write files locally, update the unit in the README (e.g. `ReadWritePaths=`).
- New env vars go into `.env.example` (documented there, in Russian); the README points to it rather than listing every variable. `ALLOWED_USER_IDS` is required, so the README's first-run step uses a placeholder `0` until the user learns their ID from the access-denied reply.
- The README's troubleshooting list quotes the exact error strings from `config.py`/`vpn_common.py` (`Переменная окружения … не задана`, `Укажите …SSH_PASSWORD или …SSH_KEY_PATH`, `Ошибка SSH (...)`). Update it if those messages change.
- Remote commands for a non-root SSH user are prefixed with `sudo -n`, so the README's sudoers example covers `bash` (WireGuard) and `docker` (Amnezia). Update it if the remote command changes.

## Architecture

Modules:

- `config.py` — loads `.env` into a frozen `Settings` with `servers: tuple[WireGuardServer | AmneziaServer, ...]`; a server is enabled when its `SSH_HOST` / `AMNEZIA_SSH_HOST` is set. Fails fast if no server is configured or a server has neither password nor key.
- `vpn_common.py` — `BaseManager` (SSH connection, `sudo -n` when user isn't root, exit-code → message mapping, `add/get/list`, add lock), `ClientConfig`, `parse_client`, `NAME_RE`, `VpnError`, `COMMON_EXIT_MESSAGES`.
- `wg_manager.py` — `WireGuardManager`: embedded bash `REMOTE_SCRIPT` run as `bash -c '<script>' wg-bot <action> <clients_dir> <name>`.
- `amnezia_manager.py` — `AmneziaManager`: embedded bash `REMOTE_SCRIPT` run inside the container as `docker exec <container> bash -c '<script>' awg-bot <action> <awg_dir> <name> <endpoint> <dns>`. Also builds the `vpn://` key for the AmneziaVPN app (`vpn_key`).
- `bot.py` — aiogram routers and handlers. Managers are injected as `servers: dict[key, BaseManager]` via `dp.start_polling(..., servers=...)`.

Key contracts to preserve:

- **Compatibility with wireguard-install.sh.** The `add` action mirrors the script's `newClient()`: reads `/etc/wireguard/params`, picks the first free `.2–.254` IP, appends a `### Client <name>` peer block to `/etc/wireguard/<nic>.conf`, writes `<clients_dir>/<nic>-client-<name>.conf`, and applies with `wg syncconf` (no restart). Changing the peer block format or file naming breaks the upstream script's List/Revoke.
- **Compatibility with the Amnezia app.** The Amnezia `add` appends a `[Peer]` to `<awg_dir>/wg0.conf` (or `awg0.conf`), applies with `wg syncconf` (or `awg` if present), copies obfuscation params (Jc/Jmin/Jmax/S1–S4/H1–H4/I1–I5) from the server `[Interface]`, uses the shared `wireguard_psk.key`, and appends `{clientId, userData.clientName}` to `clientsTable` so the client appears in the app's user list. Amnezia doesn't keep client private keys, so the bot stores configs in `<awg_dir>/bot-clients/<name>.conf`; `get` only works for bot-created clients. `list` returns raw `clientsTable` JSON, parsed in Python.
- **`vpn://` key format:** JSON → Qt `qCompress` (4-byte big-endian length + zlib) → base64url without padding. The `awg.last_config` field is itself a JSON string.
- **Remote script ↔ Python protocol.** Success output starts with a `FILE=<path>` line followed by the client config (parsed by `_parse_client`). Failures use specific exit codes mapped to Russian messages in `EXIT_MESSAGES` (common 10 invalid name, 11 exists, 12 no free IP, 13 not found in `vpn_common.COMMON_EXIT_MESSAGES`; WireGuard 20 no params file; Amnezia 21 no server conf, 22 no Address/ListenPort). Add new codes in both the script and its manager's `EXIT_MESSAGES`.
- **Name validation** is duplicated: `NAME_RE` in Python (`[A-Za-z0-9_-]{1,15}`) and `check_name` in both bash scripts. Keep them in sync.
- `add_client` is serialized by a per-server `asyncio.Lock` so concurrent requests can't pick the same IP.

Access control in `bot.py`: `admin_router` is filtered to `ALLOWED_USER_IDS`; everything else falls through to `guest_router`, which replies with "access denied" plus the user's Telegram ID (this is how users discover their ID). Both message and callback_query handlers are filtered. `/new`, `/list`, `/get` go through `start_flow` → `proceed`, which asks for whatever is missing in FSM data — server (inline buttons, skipped when only one server), app format for Amnezia (`awg` = AmneziaWG `.conf`+QR, `vpn` = AmneziaVPN `vpn://` key), then name — and then runs the action. Within `admin_router`, handler registration order matters — command handlers are registered before the `Flow.*` state handlers so commands still work mid-input, and the catch-all `fallback` is last. Messages use `ParseMode.HTML`, so escape dynamic values with `html.escape`.
