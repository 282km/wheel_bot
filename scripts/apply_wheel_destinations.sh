#!/usr/bin/env bash
# Прописать в .env на сервере чат (статистика) и канал (посты колеса).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/deploy/wheel_destinations.env"
ENV="$ROOT/.env"
if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi
if [[ ! -f "$ENV" ]]; then
  echo "Missing $ENV — create from .env.example first" >&2
  exit 1
fi
for key in TARGET_CHAT_ID WHEEL_CHANNEL_ID; do
  val="$(grep -E "^${key}=" "$SRC" | tail -n1 | cut -d= -f2- | tr -d '\r')"
  if [[ -z "$val" ]]; then
    echo "$key not set in $SRC" >&2
    exit 1
  fi
  if grep -qE "^${key}=" "$ENV"; then
    sed -i "s/^${key}=.*/${key}=${val}/" "$ENV"
  else
    echo "${key}=${val}" >>"$ENV"
  fi
done
echo "Updated $ENV:"
grep -E '^(TARGET_CHAT_ID|WHEEL_CHANNEL_ID)=' "$ENV"
