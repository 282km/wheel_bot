from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from wheel_bot import db

if TYPE_CHECKING:
    from wheel_bot.config import Settings


WHEEL_POST_TARGET_KEY = "wheel_post_target"

# Устаревшие ключи app_kv (раньше задавались из WebApp). Не читаются.
LEGACY_CFG_STATS_CHAT_ID = "cfg_stats_chat_id"
LEGACY_CFG_WHEEL_CHANNEL_ID = "cfg_wheel_channel_id"


async def get_wheel_post_target(conn) -> str:
    raw = await db.get_kv(conn, WHEEL_POST_TARGET_KEY, "channel")
    return "chat" if str(raw or "").strip() == "chat" else "channel"


async def set_wheel_post_target(conn, target: str) -> None:
    mode = str(target or "").strip().lower()
    if mode not in ("channel", "chat"):
        raise ValueError('target must be "channel" or "chat"')
    await db.set_kv(conn, WHEEL_POST_TARGET_KEY, mode)


def get_stats_chat_id(settings: "Settings") -> int:
    """Чат для /stat, истории WebApp и записей в БД — только TARGET_CHAT_ID в .env."""
    return int(settings.target_chat_id)


def get_wheel_channel_id(settings: "Settings") -> Optional[int]:
    """Канал для постов колеса — только WHEEL_CHANNEL_ID в .env."""
    return settings.wheel_channel_id


async def clear_legacy_destination_kv(conn) -> bool:
    """Удалить устаревшие переопределения ID из app_kv (если остались)."""
    changed = False
    for key in (LEGACY_CFG_STATS_CHAT_ID, LEGACY_CFG_WHEEL_CHANNEL_ID):
        row = await (
            await conn.execute("SELECT 1 FROM app_kv WHERE key = ?", (key,))
        ).fetchone()
        if row:
            await conn.execute("DELETE FROM app_kv WHERE key = ?", (key,))
            changed = True
    if changed:
        await conn.commit()
    return changed


async def resolve_wheel_destinations(
    conn,
    settings: "Settings",
) -> tuple[int, int, str]:
    """
    stats_chat_id — для БД и /stat (.env).
    post_chat_id — куда слать сообщения колеса сейчас.
    target — "channel" | "chat".
    """
    target = await get_wheel_post_target(conn)
    stats_chat_id = get_stats_chat_id(settings)
    if target == "channel":
        channel_id = get_wheel_channel_id(settings)
        if channel_id is None:
            raise ValueError(
                "Включён постинг в канал, но WHEEL_CHANNEL_ID не задан в .env на сервере. "
                "Задайте ID канала или переключите постинг в чат во вкладке «Админ»."
            )
        post_chat_id = int(channel_id)
    else:
        post_chat_id = stats_chat_id
    return stats_chat_id, post_chat_id, target


async def wheel_post_settings_payload(
    conn,
    settings: "Settings",
    target: str,
) -> dict[str, object]:
    stats_chat_id = get_stats_chat_id(settings)
    channel_chat_id = get_wheel_channel_id(settings)
    return {
        "target": target,
        "stats_chat_id": stats_chat_id,
        "channel_chat_id": channel_chat_id,
        "channel_configured": channel_chat_id is not None,
        "ids_from_env_only": True,
    }
