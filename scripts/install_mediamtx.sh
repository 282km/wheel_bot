#!/usr/bin/env bash
# Установка MediaMTX на Linux VDS (amd64).
set -euo pipefail

VERSION="${MEDIAMTX_VERSION:-v1.15.4}"
ARCH="${MEDIAMTX_ARCH:-linux_amd64}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "Downloading MediaMTX ${VERSION} (${ARCH})..."
curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${VERSION}/mediamtx_${VERSION}_${ARCH}.tar.gz" \
  -o "$TMP/mediamtx.tar.gz"
tar -xzf "$TMP/mediamtx.tar.gz" -C "$TMP"

install -m 755 "$TMP/mediamtx" /usr/local/bin/mediamtx

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
install -m 644 "$ROOT/deploy/mediamtx.yml" /etc/mediamtx.yml

cat >/etc/systemd/system/mediamtx.service <<'UNIT'
[Unit]
Description=MediaMTX RTMP/HLS server
After=network.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mediamtx /etc/mediamtx.yml
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable mediamtx
systemctl restart mediamtx
systemctl --no-pager status mediamtx

echo ""
echo "MediaMTX installed."
echo "RTMP publish: rtmp://YOUR_DOMAIN:1935/poker  (OBS: сервер rtmp://YOUR_DOMAIN:1935 , ключ poker)"
echo "Local HLS:    http://127.0.0.1:8888/poker/index.m3u8"
echo "API:          http://127.0.0.1:9997/v3/paths/list"
echo "Edit /etc/mediamtx.yml and add nginx snippet from deploy/nginx-live.conf.snippet"
