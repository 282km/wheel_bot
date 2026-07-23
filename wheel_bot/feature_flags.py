from __future__ import annotations

from typing import TYPE_CHECKING

import aiosqlite

from wheel_bot import db

if TYPE_CHECKING:
    from wheel_bot.config import Settings

KV_MINI_GAMES_ENABLED = "cfg_mini_games_enabled"


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


async def mini_games_enabled(conn: aiosqlite.Connection, settings: Settings) -> bool:
    raw = await db.get_kv(conn, KV_MINI_GAMES_ENABLED, None)
    return _parse_bool(raw, settings.mini_games_enabled)


async def set_mini_games_enabled(conn: aiosqlite.Connection, enabled: bool) -> None:
    await db.set_kv(conn, KV_MINI_GAMES_ENABLED, "1" if enabled else "0")


async def features_status(conn: aiosqlite.Connection, settings: Settings) -> dict[str, bool]:
    from wheel_bot.morning_digest_settings import load_morning_digest_config

    morning = await load_morning_digest_config(conn, settings)
    games = await mini_games_enabled(conn, settings)
    return {"games": games, "morning": morning.enabled}
