from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Всегда читаем .env из корня репозитория (не зависит от cwd systemd).
load_dotenv(_PROJECT_ROOT / ".env")


def _parse_ids(raw: Optional[str]) -> set[int]:
    if not raw:
        return set()
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        out.add(int(part))
    return out


@dataclass(frozen=True)
class Settings:
    bot_token: str
    session_secret: str
    public_base_url: str
    target_chat_id: int
    wheel_channel_id: Optional[int]
    superadmin_ids: set[int]
    http_host: str
    http_port: int
    static_dir: Path
    database_path: Path
    forwarded_allow_ips: str
    ssl_certfile: Optional[str]
    ssl_keyfile: Optional[str]
    webhook_path: str
    webhook_secret: str

    @property
    def webapp_url(self) -> str:
        base = self.public_base_url.rstrip("/")
        return f"{base}/webapp/"


def load_settings() -> Settings:
    root = _PROJECT_ROOT
    static_dir = root / "static"
    db_path = Path(os.getenv("DATABASE_PATH", str(root / "data" / "app.db"))).expanduser()

    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError("BOT_TOKEN is required")

    chat_raw = os.getenv("TARGET_CHAT_ID", "").strip()
    if not chat_raw:
        raise RuntimeError(
            f"TARGET_CHAT_ID is required in {_PROJECT_ROOT / '.env'} "
            "(пустое значение или переменная не задана)"
        )
    try:
        target_chat_id = int(chat_raw)
    except ValueError as e:
        raise RuntimeError(f"TARGET_CHAT_ID must be an integer, got: {chat_raw!r}") from e

    channel_raw = os.getenv("WHEEL_CHANNEL_ID", "").strip()
    wheel_channel_id: Optional[int] = None
    if channel_raw:
        try:
            wheel_channel_id = int(channel_raw)
        except ValueError as e:
            raise RuntimeError(f"WHEEL_CHANNEL_ID must be an integer, got: {channel_raw!r}") from e

    session_secret = os.getenv("SESSION_SECRET", "").strip()
    if len(session_secret) < 16:
        raise RuntimeError("SESSION_SECRET must be at least 16 characters")

    public_base = os.getenv("PUBLIC_BASE_URL", "").strip().rstrip("/")
    if not public_base:
        raise RuntimeError("PUBLIC_BASE_URL is required (https URL for WebApp)")

    # За nginx/Caddy/Cloudflare: иначе Uvicorn может отвечать
    # «Rejected request from RFC1918 IP to public server address».
    forwarded_allow_ips = os.getenv("FORWARDED_ALLOW_IPS", "*").strip() or "*"

    ssl_cert = os.getenv("SSL_CERTFILE", "").strip() or None
    ssl_key = os.getenv("SSL_KEYFILE", "").strip() or None
    if (ssl_cert is None) ^ (ssl_key is None):
        raise RuntimeError("Задайте оба параметра SSL_CERTFILE и SSL_KEYFILE или ни одного")

    webhook_path = os.getenv("WEBHOOK_PATH", "/telegram/webhook").strip()
    if not webhook_path.startswith("/"):
        webhook_path = f"/{webhook_path}"

    webhook_secret = os.getenv("WEBHOOK_SECRET", "").strip()
    if len(webhook_secret) < 8:
        raise RuntimeError("WEBHOOK_SECRET must be at least 8 characters")

    return Settings(
        bot_token=token,
        session_secret=session_secret,
        public_base_url=public_base,
        target_chat_id=target_chat_id,
        wheel_channel_id=wheel_channel_id,
        superadmin_ids=_parse_ids(os.getenv("SUPERADMIN_IDS")),
        http_host=os.getenv("HTTP_HOST", "0.0.0.0"),
        http_port=int(os.getenv("HTTP_PORT", "8080")),
        static_dir=static_dir,
        database_path=db_path,
        forwarded_allow_ips=forwarded_allow_ips,
        ssl_certfile=ssl_cert,
        ssl_keyfile=ssl_key,
        webhook_path=webhook_path,
        webhook_secret=webhook_secret,
    )
