from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from wheel_bot import db

if TYPE_CHECKING:
    from wheel_bot.config import Settings


WHEEL_POST_TARGET_KEY = "wheel_post_target"
CFG_STATS_CHAT_ID = "cfg_stats_chat_id"
CFG_WHEEL_CHANNEL_ID = "cfg_wheel_channel_id"


async def get_wheel_post_target(conn) -> str:
    raw = await db.get_kv(conn, WHEEL_POST_TARGET_KEY, "channel")
    return "chat" if str(raw or "").strip() == "chat" else "channel"


async def set_wheel_post_target(conn, target: str) -> None:
    mode = str(target or "").strip().lower()
    if mode not in ("channel", "chat"):
        raise ValueError('target must be "channel" or "chat"')
    await db.set_kv(conn, WHEEL_POST_TARGET_KEY, mode)


async def get_effective_stats_chat_id(conn, settings: "Settings") -> int:
    raw = await db.get_kv(conn, CFG_STATS_CHAT_ID, None)
    if raw is not None and str(raw).strip():
        return int(str(raw).strip())
    return int(settings.target_chat_id)


async def get_effective_wheel_channel_id(conn, settings: "Settings") -> Optional[int]:
    raw = await db.get_kv(conn, CFG_WHEEL_CHANNEL_ID, None)
    if raw is not None:
        s = str(raw).strip()
        if not s or s.lower() in ("none", "null"):
            return None
        return int(s)
    return settings.wheel_channel_id


async def set_configured_chat_ids(
    conn,
    stats_chat_id: int,
    channel_chat_id: Optional[int],
) -> None:
    await db.set_kv(conn, CFG_STATS_CHAT_ID, str(int(stats_chat_id)))
    if channel_chat_id is None:
        await db.set_kv(conn, CFG_WHEEL_CHANNEL_ID, "")
    else:
        await db.set_kv(conn, CFG_WHEEL_CHANNEL_ID, str(int(channel_chat_id)))


async def resolve_wheel_destinations(
    conn,
    settings: "Settings",
) -> tuple[int, int, str]:
    """
    stats_chat_id — для БД и /stat.
    post_chat_id — куда слать сообщения колеса сейчас.
    target — "channel" | "chat".
    """
    target = await get_wheel_post_target(conn)
    stats_chat_id = await get_effective_stats_chat_id(conn, settings)
    if target == "channel":
        channel_id = await get_effective_wheel_channel_id(conn, settings)
        if channel_id is None:
            raise ValueError(
                "Включён постинг в канал, но ID канала не задан. "
                "Укажите его во вкладке «Админ» или переключите постинг в чат."
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
    stats_chat_id = await get_effective_stats_chat_id(conn, settings)
    channel_chat_id = await get_effective_wheel_channel_id(conn, settings)
    return {
        "target": target,
        "stats_chat_id": stats_chat_id,
        "channel_chat_id": channel_chat_id,
        "channel_configured": channel_chat_id is not None,
        "ids_from_env": {
            "stats_chat_id": int(settings.target_chat_id),
            "channel_chat_id": settings.wheel_channel_id,
        },
    }
