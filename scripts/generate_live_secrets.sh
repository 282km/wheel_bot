#!/usr/bin/env bash
# Генерация случайного path и пароля RTMP для /live.
# Использование: bash scripts/generate_live_secrets.sh
set -u

if command -v openssl >/dev/null 2>&1; then
  PATH_NAME="table_$(openssl rand -hex 6)"
  RTMP_PASS="$(openssl rand -base64 24 | tr -d '/+=' | head -c 32)"
else
  PATH_NAME="table_$(date +%s | sha256sum | head -c 12)"
  RTMP_PASS="$(date +%s | sha256sum | head -c 32)"
fi

cat <<EOF
=== Скопируйте в /opt/wheel_bot_git/.env ===

LIVE_STREAM_ENABLED=1
LIVE_STREAM_PATH=${PATH_NAME}
LIVE_MEDIAMTX_API=http://127.0.0.1:9997
LIVE_STREAM_TITLE=Покерный стол

=== В /etc/mediamtx.yml (paths + пароль) ===

paths:
  ${PATH_NAME}:
    source: publisher
    publishUser: obs
    publishPass: ${RTMP_PASS}
    # publishIPs: ["ВАШ_IP/32"]

=== OBS ===

Сервер:  rtmp://kolesosychat.fun:1935/${PATH_NAME}
Ключ:    (пусто)
Логин:   obs
Пароль:  ${RTMP_PASS}

=== После правок ===

sudo systemctl restart mediamtx
sudo systemctl restart wheel-bot

Проверка (OBS включён):
curl -s http://127.0.0.1:9997/v3/paths/get/${PATH_NAME} | grep ready

EOF
