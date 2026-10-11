FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Тот же каталог, что и при установке службой systemd: пути в .env
# (например, SSH_KEY_PATH=/opt/wg-bot/.ssh/id_ed25519) подходят для обоих способов.
WORKDIR /opt/wg-bot

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY *.py ./

# Непривилегированный пользователь; UID нужен, чтобы выдать ему права на SSH-ключ на хосте
ARG APP_UID=10001
RUN useradd --system --uid "${APP_UID}" --home-dir /opt/wg-bot --shell /usr/sbin/nologin wgbot
USER wgbot

CMD ["python", "bot.py"]
