# WireGuard Telegram Bot

Telegram-бот для создания клиентов WireGuard на сервере, где WireGuard установлен через
[angristan/wireguard-install](https://github.com/angristan/wireguard-install).

Бот подключается к серверу по SSH и делает то же, что пункт «1) Add a new user» скрипта:
- выбирает свободные IPv4/IPv6 в подсети WireGuard;
- генерирует ключи и добавляет peer в `/etc/wireguard/wg0.conf`;
- применяет изменения без перезапуска (`wg syncconf`);
- сохраняет конфиг в `/root/wg0-client-<имя>.conf`;
- присылает в Telegram `.conf`-файл и QR-код.

Формат записей совпадает со скриптом, поэтому `./wireguard-install.sh` по-прежнему видит
клиентов, созданных ботом, и умеет их удалять (Revoke).

## Сервер Amnezia VPN

Можно подключить второй сервер — с Amnezia VPN, установленным из приложения Amnezia
(Docker-контейнер `amnezia-awg`, протокол AmneziaWG). Для этого заполните переменные
`AMNEZIA_*` в `.env`. Если задано несколько серверов, бот спрашивает кнопками, где создать
клиента, а для Amnezia — ещё и для какого приложения нужен конфиг:

- **AmneziaWG** — `.conf`-файл и QR-код для приложения AmneziaWG;
- **AmneziaVPN** — ключ `vpn://…` (сообщением и файлом `.vpn`) для приложения AmneziaVPN.

Бот выполняет команды внутри контейнера через `docker exec`: добавляет `[Peer]` в
`/opt/amnezia/awg/wg0.conf`, применяет его через `wg syncconf` (без перезапуска) и
записывает клиента в `clientsTable`, поэтому он виден в разделе «Пользователи» приложения
Amnezia и удаляется оттуда же. Параметры обфускации (Jc, Jmin, Jmax, S1, S2, H1–H4)
берутся из конфига сервера.

Amnezia не хранит приватные ключи клиентов, поэтому бот сохраняет созданные им конфиги в
`/opt/amnezia/awg/bot-clients/` внутри контейнера — `/get` работает только для клиентов,
созданных ботом. При переустановке контейнера из приложения Amnezia эти файлы пропадут.

Если SSH-пользователь не root, ему нужен `sudo` без пароля для `docker`.

## Команды

| Команда | Что делает |
|---|---|
| `/new` | создать клиента (бот спросит сервер, приложение и имя) |
| `/new Test001-name` | создать клиента с этим именем (сервер и приложение — кнопками) |
| `/list` | список клиентов выбранного сервера |
| `/get` | повторно прислать конфиг (бот спросит сервер, приложение и имя) |
| `/get Test001-name` | повторно прислать конфиг клиента с этим именем |
| `/cancel` | отменить ввод |
| `/help`, `/start` | краткая справка |

Имя клиента: только латиница, цифры, `_` и `-`, не длиннее 15 символов.

## Как проходит диалог

После команды `/new`, `/get` или `/list` бот по очереди спрашивает только то, чего ему
не хватает, и сразу выполняет действие, когда всё известно:

```
/new [имя]  ·  /get [имя]  ·  /list
      │
      ▼
1. Сервер       кнопки «WireGuard» / «Amnezia»
      │         (шаг пропускается, если настроен только один сервер)
      ▼
2. Приложение   кнопки «AmneziaWG» / «AmneziaVPN»
      │         (только для сервера Amnezia и только для /new и /get)
      ▼
3. Имя          вводится текстом
      │         (шаг пропускается, если имя указано в команде, и для /list)
      ▼
Результат: конфиг, ключ vpn:// или список клиентов
```

### 1. Выбор сервера

Сервер выбирается **первым**, сразу после команды и ещё до ввода имени. Даже если имя
указано в команде (`/new Test001-name`), бот всё равно покажет кнопки выбора сервера.

- На кнопках показаны названия серверов из `WG_SERVER_NAME` и `AMNEZIA_SERVER_NAME`
  (по умолчанию «WireGuard» и «Amnezia»).
- После нажатия сообщение с кнопками меняется на «Сервер: <название>», и бот переходит
  к следующему шагу.
- Если в `.env` настроен только один сервер (задан только `SSH_HOST` или только
  `AMNEZIA_SSH_HOST`), бот ничего не спрашивает и работает с ним. Какой сервер
  использован, видно в сообщении «⏳ Создаю клиента … на «…»» и в подписи к конфигу.

### 2. Выбор приложения (только Amnezia)

Если выбран сервер Amnezia, бот спрашивает «Для какого приложения сделать конфиг?»:

- **AmneziaWG** — `.conf`-файл и QR-код;
- **AmneziaVPN** — файл `<имя>.vpn` и отдельным сообщением ключ `vpn://…`. Ключ можно
  вставить в приложении AmneziaVPN через «Добавить сервер».

Для сервера WireGuard этого шага нет, и бот всегда присылает `.conf` и QR-код для
приложения WireGuard. Для `/list` приложение тоже не спрашивается.

### 3. Ввод имени

Если имя не указано в команде, бот просит прислать его отдельным сообщением. Если имя
недопустимое, бот объяснит правила и подождёт новое. Если недопустимое имя указано прямо
в команде (`/new имя!`), бот сразу ответит ошибкой, не спрашивая сервер.

### Примеры

Два сервера, новый клиент для приложения AmneziaVPN:

```
Вы:   /new
Бот:  Выберите сервер:            [WireGuard] [Amnezia]
Вы:   нажимаете «Amnezia»
Бот:  Для какого приложения сделать конфиг?   [AmneziaWG] [AmneziaVPN]
Вы:   нажимаете «AmneziaVPN»
Бот:  Введите имя нового клиента.
Вы:   phone-anna
Бот:  ✅ Клиент создан + файл phone-anna.vpn + ключ vpn://…
```

Два сервера, имя указано в команде, сервер WireGuard:

```
Вы:   /new laptop
Бот:  Выберите сервер:            [WireGuard] [Amnezia]
Вы:   нажимаете «WireGuard»
Бот:  ✅ Клиент создан + laptop.conf + QR-код
```

### Что важно знать

- Пока бот ждёт нажатия кнопки, текстовые сообщения не принимаются: бот ответит
  «Выберите вариант кнопкой выше или /cancel».
- Команды работают на любом шаге: новая `/new`, `/get` или `/list` начинает диалог
  заново, `/cancel` его отменяет.
- Кнопки из старых сообщений не срабатывают: бот покажет «Кнопка устарела, начните
  заново».
- После выполнения действия диалог завершается. Для следующего клиента нужно снова
  отправить `/new`.

## Установка и запуск как службы

Рекомендуемый способ — запустить бота на Linux-сервере как службу systemd. Тогда бот
стартует вместе с системой, сам перезапускается после сбоев, а его логи пишутся в журнал
systemd.

Бот может работать где угодно, откуда есть доступ к Telegram и SSH-доступ к VPN-серверам.
Проще всего поставить его на тот же VPS, где установлен WireGuard или Amnezia, — тогда бот
подключается к `127.0.0.1`. Ниже все команды выполняются от `root` на Debian/Ubuntu; бот
ставится в `/opt/wg-bot` и работает от отдельного непривилегированного пользователя
`wgbot`.

### Требования

- Linux с systemd (Debian 11+, Ubuntu 22.04+ и т. п.);
- Python 3.10 или новее (`python3 --version`);
- сервер с WireGuard (через `wireguard-install.sh`) и/или Amnezia VPN, к которому есть
  SSH-доступ под `root` или под пользователем с `sudo` без пароля;
- исходящий доступ к `api.telegram.org` (бот работает через long polling, входящие порты
  открывать не нужно).

### Шаг 1. Создайте бота в Telegram

1. Откройте [@BotFather](https://t.me/BotFather), отправьте `/newbot` и задайте имя.
2. Сохраните токен вида `123456789:AAAA...` — он понадобится для `BOT_TOKEN`.

Свой Telegram ID пока знать не нужно: бот покажет его сам на шаге 5.

### Шаг 2. Скачайте бота и установите зависимости

```bash
apt update && apt install -y git python3 python3-venv

git clone https://github.com/ierehon91/CustomVpnTelegramManager.git /opt/wg-bot
cd /opt/wg-bot

python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Создайте системного пользователя, от которого будет работать служба:

```bash
useradd --system --home-dir /opt/wg-bot --shell /usr/sbin/nologin wgbot
```

### Шаг 3. Настройте SSH-доступ к VPN-серверу

Бот подключается к серверу по SSH. Можно указать пароль (`SSH_PASSWORD`), но надёжнее
отдельный ключ без парольной фразы:

```bash
mkdir -p /opt/wg-bot/.ssh
ssh-keygen -t ed25519 -N "" -C wg-bot -f /opt/wg-bot/.ssh/id_ed25519
```

Добавьте публичный ключ на VPN-сервер:

```bash
# бот на том же сервере
cat /opt/wg-bot/.ssh/id_ed25519.pub >> /root/.ssh/authorized_keys

# бот на другой машине
ssh-copy-id -i /opt/wg-bot/.ssh/id_ed25519.pub root@1.2.3.4
```

Для входа под `root` по ключу в `/etc/ssh/sshd_config` на VPN-сервере должно быть
`PermitRootLogin prohibit-password` (или `yes`). На большинстве дистрибутивов это значение
по умолчанию.

Сохраните ключ сервера в `known_hosts`, чтобы бот проверял, что подключается именно к нему:

```bash
ssh-keyscan -p 22 1.2.3.4 > /opt/wg-bot/.ssh/known_hosts      # или 127.0.0.1
ssh-keygen -lf /opt/wg-bot/.ssh/known_hosts                   # сверьте отпечатки
```

`ssh-keyscan` ничего не проверяет сам — сравните отпечатки с выводом
`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub` на VPN-сервере. Если серверов два
(WireGuard и Amnezia), добавьте оба в один файл через `>>`.

Проверьте, что подключение работает:

```bash
ssh -i /opt/wg-bot/.ssh/id_ed25519 -o UserKnownHostsFile=/opt/wg-bot/.ssh/known_hosts \
    root@1.2.3.4 'wg show interfaces'
```

#### Если SSH-пользователь не root

Бот выполняет команды через `sudo -n`, поэтому пользователю нужен `sudo` без пароля:
для WireGuard — на `bash` (скрипт работает с `/etc/wireguard`), для Amnezia — на `docker`.
Например, для пользователя `vpnadmin` создайте `/etc/sudoers.d/wg-bot` командой
`visudo -f /etc/sudoers.d/wg-bot`:

```
vpnadmin ALL=(root) NOPASSWD: /usr/bin/bash, /usr/bin/docker
```

Оставьте только нужную строку. Учтите, что `sudo bash` фактически даёт полный доступ
`root`, так что такой пользователь не безопаснее самого `root`.

### Шаг 4. Заполните `.env`

```bash
cp /opt/wg-bot/.env.example /opt/wg-bot/.env
nano /opt/wg-bot/.env
```

Все переменные описаны в [`.env.example`](.env.example). Пример для бота на том же VPS,
где WireGuard установлен через `wireguard-install.sh`:

```ini
BOT_TOKEN=123456789:AAAA...
ALLOWED_USER_IDS=0               # свой ID впишете на шаге 5

SSH_HOST=127.0.0.1
SSH_PORT=22
SSH_USER=root
SSH_KEY_PATH=/opt/wg-bot/.ssh/id_ed25519
SSH_KNOWN_HOSTS=/opt/wg-bot/.ssh/known_hosts
WG_CLIENTS_DIR=/root
WG_SERVER_NAME=WireGuard
```

Для сервера Amnezia заполните блок `AMNEZIA_*` так же. Обратите внимание на
`AMNEZIA_ENDPOINT`: если бот подключается к Amnezia по `127.0.0.1`, укажите здесь внешний
IP или домен сервера, иначе в клиентских конфигах окажется `127.0.0.1`. Если какой-то сервер
не нужен, оставьте пустым его `SSH_HOST` / `AMNEZIA_SSH_HOST`.

`.env` содержит токен бота и, возможно, SSH-пароль, поэтому закройте его и ключ от чужих
глаз и отдайте каталог пользователю службы:

```bash
chown -R wgbot:wgbot /opt/wg-bot
chmod 700 /opt/wg-bot/.ssh
chmod 600 /opt/wg-bot/.env /opt/wg-bot/.ssh/id_ed25519
```

### Шаг 5. Первый запуск и Telegram ID

Запустите бота вручную от пользователя службы — так сразу видно ошибки настройки:

```bash
cd /opt/wg-bot
sudo -u wgbot .venv/bin/python bot.py
```

Если в логе нет ошибок и есть строка `Start polling`, напишите боту что-нибудь в Telegram.
Он ответит «⛔ Доступ запрещён» и покажет ваш Telegram ID. Остановите бота (`Ctrl+C`),
замените `0` в `ALLOWED_USER_IDS` на свой ID (несколько ID — через запятую) и переходите к службе.

### Шаг 6. Создайте службу systemd

Создайте файл `/etc/systemd/system/wg-bot.service`:

```ini
[Unit]
Description=WireGuard / Amnezia Telegram bot
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=wgbot
Group=wgbot
WorkingDirectory=/opt/wg-bot
ExecStart=/opt/wg-bot/.venv/bin/python bot.py
Environment=PYTHONUNBUFFERED=1
Restart=always
RestartSec=10

# Ограничения: бот не пишет на диск и не нуждается в привилегиях
NoNewPrivileges=true
ProtectSystem=strict
ProtectHome=true
PrivateTmp=true
PrivateDevices=true

[Install]
WantedBy=multi-user.target
```

Что здесь важно:

- `WorkingDirectory` — бот читает `.env` из текущего каталога, поэтому он должен указывать
  на папку с ботом.
- `ExecStart` — Python из виртуального окружения, а не системный: иначе не найдутся
  зависимости.
- `User=wgbot` — служба работает без прав root. Права на VPN-сервере бот получает через
  SSH, а не от локального пользователя.
- `Restart=always` и `RestartSec=10` — при падении или потере сети бот перезапустится
  через 10 секунд.
- Блок ограничений можно убрать, если он мешает (например, ключ лежит вне `/opt/wg-bot`
  в `/home` — тогда уберите `ProtectHome=true`).

Включите автозапуск и запустите службу:

```bash
systemctl daemon-reload
systemctl enable --now wg-bot
systemctl status wg-bot
```

В статусе должно быть `active (running)`. Отправьте боту `/list` — он должен показать
список клиентов.

### Управление службой

| Команда | Что делает |
|---|---|
| `systemctl status wg-bot` | состояние службы и последние строки лога |
| `systemctl restart wg-bot` | перезапуск (нужен после изменения `.env`) |
| `systemctl stop wg-bot` | остановить |
| `systemctl disable --now wg-bot` | остановить и убрать из автозапуска |
| `journalctl -u wg-bot -f` | смотреть лог в реальном времени |
| `journalctl -u wg-bot -n 100 --no-pager` | последние 100 строк лога |
| `journalctl -u wg-bot --since "1 hour ago"` | лог за последний час |

После правки самого файла `wg-bot.service` выполните `systemctl daemon-reload` и
`systemctl restart wg-bot`.

### Обновление

```bash
cd /opt/wg-bot
sudo -u wgbot git pull
sudo -u wgbot .venv/bin/pip install -r requirements.txt
systemctl restart wg-bot
```

`.env` и ключи не хранятся в git и при обновлении не меняются. Если в новой версии
появились переменные, сравните свой `.env` с `.env.example`.

### Если что-то не работает

Сначала посмотрите лог: `journalctl -u wg-bot -n 50 --no-pager`.

- **`Переменная окружения BOT_TOKEN не задана`** — `.env` не найден или не читается:
  проверьте `WorkingDirectory` в службе и владельца файла (`ls -l /opt/wg-bot/.env`).
- **`Укажите SSH_PASSWORD или SSH_KEY_PATH`** — для сервера задан `SSH_HOST`, но нет ни
  пароля, ни ключа.
- **`Ошибка SSH (...)`** в ответе бота — повторите проверку подключения из шага 3 от
  пользователя службы: `sudo -u wgbot ssh -i /opt/wg-bot/.ssh/id_ed25519 ...`. Сообщение
  `Host key is not trusted` значит, что сервера нет в `SSH_KNOWN_HOSTS` или его ключ
  изменился.
- **`sudo: a password is required`** — SSH-пользователь не root и у него нет `sudo` без
  пароля (см. «Если SSH-пользователь не root»).
- **`Conflict: terminated by other getUpdates request`** — с этим токеном запущен второй
  экземпляр бота: забытый ручной запуск из шага 5 или бот на другой машине. Остановите
  лишний.
- **Бот отвечает «Доступ запрещён» вам самим** — ваш ID не попал в `ALLOWED_USER_IDS` или
  служба не перезапущена после правки `.env`.
- **Служба постоянно перезапускается** — бот падает при старте; причина видна в логе.

### Запуск без службы (для разработки)

Для проверки на своём компьютере достаточно запустить бота в терминале:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env             # и заполнить
python bot.py
```

Бот работает, пока открыт терминал. Не запускайте его одновременно со службой на сервере с
тем же токеном — Telegram отдаёт обновления только одному экземпляру.

## Безопасность

- Бот отвечает только пользователям из `ALLOWED_USER_IDS`.
- Конфиг содержит приватный ключ клиента: не пересылайте его посторонним.
- Если `SSH_KNOWN_HOSTS` не задан, ключ сервера не проверяется. Лучше указать путь к
  `known_hosts`, где уже есть запись о сервере (например, `~/.ssh/known_hosts` после
  первого подключения через `ssh`).
