from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

from wheel_bot.posting import (
    get_stats_chat_id,
    get_wheel_channel_id,
    get_wheel_post_target,
    resolve_wheel_destinations,
)

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.destinations")


async def read_destination_config(conn, settings: "Settings") -> dict[str, object]:
    stats_chat_id = get_stats_chat_id(settings)
    channel_chat_id = get_wheel_channel_id(settings)
    post_target = await get_wheel_post_target(conn)
    _, post_chat_id, _ = await resolve_wheel_destinations(conn, settings)
    return {
        "stats_chat_id": stats_chat_id,
        "channel_chat_id": channel_chat_id,
        "post_target": post_target,
        "post_chat_id": post_chat_id,
    }


def validate_destination_ids(stats_chat_id: int, channel_chat_id: Optional[int]) -> None:
    if channel_chat_id is not None and int(stats_chat_id) == int(channel_chat_id):
        raise ValueError(
            "TARGET_CHAT_ID и WHEEL_CHANNEL_ID в .env совпадают. "
            "Чат — для /stat и истории, канал — только для постов колеса."
        )


async def log_effective_destinations(conn, settings: "Settings") -> None:
    cfg = await read_destination_config(conn, settings)
    log.info("STATS chat (/stat, DB) from .env TARGET_CHAT_ID: %s", cfg["stats_chat_id"])
    log.info("WHEEL channel from .env WHEEL_CHANNEL_ID: %s", cfg["channel_chat_id"])
    log.info("Post mode: %s -> messages to chat_id %s", cfg["post_target"], cfg["post_chat_id"])
    if cfg["channel_chat_id"] is not None and int(cfg["stats_chat_id"]) == int(cfg["channel_chat_id"]):
        log.error("Misconfiguration: TARGET_CHAT_ID equals WHEEL_CHANNEL_ID in .env")
