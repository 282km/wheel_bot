from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from wheel_bot import db
from wheel_bot.posting import (
    CFG_STATS_CHAT_ID,
    CFG_WHEEL_CHANNEL_ID,
    get_effective_stats_chat_id,
    get_effective_wheel_channel_id,
    get_wheel_post_target,
    resolve_wheel_destinations,
)

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.destinations")


async def read_destination_config(conn, settings: "Settings") -> dict[str, object]:
    stats_chat_id = await get_effective_stats_chat_id(conn, settings)
    channel_chat_id = await get_effective_wheel_channel_id(conn, settings)
    post_target = await get_wheel_post_target(conn)
    _, post_chat_id, _ = await resolve_wheel_destinations(conn, settings)
    kv_stats = await db.get_kv(conn, CFG_STATS_CHAT_ID, None)
    kv_channel = await db.get_kv(conn, CFG_WHEEL_CHANNEL_ID, None)
    return {
        "stats_chat_id": stats_chat_id,
        "channel_chat_id": channel_chat_id,
        "post_target": post_target,
        "post_chat_id": post_chat_id,
        "env_stats_chat_id": int(settings.target_chat_id),
        "env_channel_chat_id": settings.wheel_channel_id,
        "kv_stats_chat_id": kv_stats,
        "kv_channel_chat_id": kv_channel,
    }


def validate_destination_ids(stats_chat_id: int, channel_chat_id: Optional[int]) -> None:
    if channel_chat_id is not None and int(stats_chat_id) == int(channel_chat_id):
        raise ValueError(
            "ID чата и ID канала совпадают. "
            "Чат — для /stat и истории, канал — только для постов колеса."
        )


async def log_effective_destinations(conn, settings: "Settings") -> None:
    cfg = await read_destination_config(conn, settings)
    log.info("STATS chat (/stat, DB): %s", cfg["stats_chat_id"])
    log.info("WHEEL channel (configured): %s", cfg["channel_chat_id"])
    log.info("Post mode: %s -> messages to chat_id %s", cfg["post_target"], cfg["post_chat_id"])
    if cfg["kv_stats_chat_id"]:
        log.info("  (override app_kv %s=%s)", CFG_STATS_CHAT_ID, cfg["kv_stats_chat_id"])
    if cfg["kv_channel_chat_id"]:
        log.info("  (override app_kv %s=%r)", CFG_WHEEL_CHANNEL_ID, cfg["kv_channel_chat_id"])
    if cfg["channel_chat_id"] is not None and int(cfg["stats_chat_id"]) == int(cfg["channel_chat_id"]):
        log.error("Misconfiguration: stats chat id equals channel id — /stat and posts will conflict")
