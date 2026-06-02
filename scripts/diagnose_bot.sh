#!/usr/bin/env bash
# Быстрая диагностика падения wheel-bot на сервере.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT" || exit 1

echo "=== paths ==="
pwd
ls -la .env 2>/dev/null || echo "NO .env file"

echo ""
echo "=== TARGET_CHAT_ID in .env ==="
grep -n 'TARGET_CHAT_ID' .env 2>/dev/null || echo "(no TARGET_CHAT_ID line)"

echo ""
echo "=== systemd (override env?) ==="
systemctl cat wheel-bot 2>/dev/null | grep -E 'Environment|WorkingDirectory|ExecStart' || true

echo ""
echo "=== service status ==="
systemctl is-active wheel-bot 2>/dev/null || true
systemctl status wheel-bot --no-pager -l 2>/dev/null | tail -n 25 || true

echo ""
echo "=== last logs ==="
journalctl -u wheel-bot -n 40 --no-pager 2>/dev/null || true

echo ""
echo "=== python check ==="
if [[ -x .venv/bin/python ]]; then
  PY=.venv/bin/python
elif command -v python3 >/dev/null; then
  PY=python3
else
  echo "python not found"
  exit 1
fi
$PY scripts/verify_startup.py
