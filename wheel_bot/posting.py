from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from wheel_bot import db

if TYPE_CHECKING:
    from wheel_bot.config import Settings


WHEEL_POST_TARGET_KEY = "wheel_post_target"


async def get_wheel_post_target(conn) -> str:
    raw = await db.get_kv(conn, WHEEL_POST_TARGET_KEY, "channel")
    return "chat" if str(raw or "").strip() == "chat" else "channel"


async def set_wheel_post_target(conn, target: str) -> None:
    mode = str(target or "").strip().lower()
    if mode not in ("channel", "chat"):
        raise ValueError('target must be "channel" or "chat"')
    await db.set_kv(conn, WHEEL_POST_TARGET_KEY, mode)


async def resolve_wheel_destinations(
    conn,
    settings: "Settings",
) -> tuple[int, int, str]:
    """
    stats_chat_id — для БД и /stat (TARGET_CHAT_ID).
    post_chat_id — куда слать сообщения колеса сейчас.
  target — "channel" | "chat".
    """
    target = await get_wheel_post_target(conn)
    stats_chat_id = int(settings.target_chat_id)
    if target == "channel":
        if settings.wheel_channel_id is None:
            raise ValueError(
                "Включён постинг в канал, но WHEEL_CHANNEL_ID не задан в .env. "
                "Добавьте id канала или переключите постинг в чат."
            )
        post_chat_id = int(settings.wheel_channel_id)
    else:
        post_chat_id = stats_chat_id
    return stats_chat_id, post_chat_id, target


def wheel_post_settings_payload(
    settings: "Settings",
    target: str,
) -> dict[str, object]:
    return {
        "target": target,
        "stats_chat_id": int(settings.target_chat_id),
        "channel_chat_id": settings.wheel_channel_id,
        "channel_configured": settings.wheel_channel_id is not None,
    }
