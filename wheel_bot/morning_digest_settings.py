from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Optional

import aiosqlite

from wheel_bot import db

if TYPE_CHECKING:
    from wheel_bot.config import Settings

KV_OPENAI_API_KEY = "cfg_openai_api_key"
KV_MORNING_DIGEST_ENABLED = "cfg_morning_digest_enabled"
KV_OPENAI_MODEL = "cfg_openai_model"
KV_MORNING_DIGEST_HOUR = "cfg_morning_digest_hour"


@dataclass(frozen=True)
class MorningDigestConfig:
    api_key: Optional[str]
    enabled: bool
    model: str
    hour: int
    timezone: str
    target_chat_id: int
    source: str  # env | db | none


def mask_api_key(key: Optional[str]) -> str:
    if not key:
        return ""
    k = str(key).strip()
    if len(k) <= 10:
        return "••••••••"
    return f"{k[:7]}...{k[-4:]}"


def _parse_bool(raw: Optional[str], default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def load_morning_digest_config(conn: aiosqlite.Connection, settings: Settings) -> MorningDigestConfig:
    db_key = (await db.get_kv(conn, KV_OPENAI_API_KEY, "") or "").strip()
    env_key = (settings.openai_api_key or "").strip()
    if db_key:
        api_key: Optional[str] = db_key
        source = "db"
    elif env_key:
        api_key = env_key
        source = "env"
    else:
        api_key = None
        source = "none"

    enabled = _parse_bool(await db.get_kv(conn, KV_MORNING_DIGEST_ENABLED, None), settings.morning_digest_enabled)
    model = (await db.get_kv(conn, KV_OPENAI_MODEL, "") or "").strip() or settings.openai_model
    hour_raw = (await db.get_kv(conn, KV_MORNING_DIGEST_HOUR, "") or "").strip()
    hour = int(hour_raw) if hour_raw.isdigit() else settings.morning_digest_hour

    return MorningDigestConfig(
        api_key=api_key or None,
        enabled=enabled,
        model=model,
        hour=hour,
        timezone=settings.morning_digest_timezone,
        target_chat_id=int(settings.target_chat_id),
        source=source,
    )


async def save_morning_digest_settings(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    openai_api_key: Optional[str] = None,
    clear_api_key: bool = False,
    enabled: Optional[bool] = None,
    model: Optional[str] = None,
    hour: Optional[int] = None,
) -> MorningDigestConfig:
    if clear_api_key:
        await db.set_kv(conn, KV_OPENAI_API_KEY, "")
    elif openai_api_key is not None:
        key = str(openai_api_key).strip()
        if key:
            await db.set_kv(conn, KV_OPENAI_API_KEY, key)

    if enabled is not None:
        await db.set_kv(conn, KV_MORNING_DIGEST_ENABLED, "1" if enabled else "0")

    if model is not None:
        m = str(model).strip() or settings.openai_model
        await db.set_kv(conn, KV_OPENAI_MODEL, m)

    if hour is not None:
        h = int(hour)
        if not 0 <= h <= 23:
            raise ValueError("hour must be between 0 and 23")
        await db.set_kv(conn, KV_MORNING_DIGEST_HOUR, str(h))

    return await load_morning_digest_config(conn, settings)


async def morning_digest_settings_payload(conn: aiosqlite.Connection, settings: Settings) -> dict[str, Any]:
    cfg = await load_morning_digest_config(conn, settings)
    return {
        "enabled": cfg.enabled,
        "hour": cfg.hour,
        "timezone": cfg.timezone,
        "ready": bool(cfg.enabled),
    }
