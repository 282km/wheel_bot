#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/deploy/target_chat_id.env"
ENV="$ROOT/.env"
if [[ ! -f "$SRC" ]]; then
  echo "Missing $SRC" >&2
  exit 1
fi
NEW_ID="$(grep -E '^TARGET_CHAT_ID=' "$SRC" | tail -n1 | cut -d= -f2- | tr -d '\r')"
if [[ -z "$NEW_ID" ]]; then
  echo "TARGET_CHAT_ID not set in $SRC" >&2
  exit 1
fi
if [[ ! -f "$ENV" ]]; then
  echo "Missing $ENV — create from .env.example first" >&2
  exit 1
fi
if grep -qE '^TARGET_CHAT_ID=' "$ENV"; then
  sed -i "s/^TARGET_CHAT_ID=.*/TARGET_CHAT_ID=$NEW_ID/" "$ENV"
else
  echo "TARGET_CHAT_ID=$NEW_ID" >>"$ENV"
fi
echo "Updated $ENV:"
grep -E '^TARGET_CHAT_ID=' "$ENV"
