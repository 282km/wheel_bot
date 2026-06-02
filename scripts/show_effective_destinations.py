#!/usr/bin/env python3
"""Показать, куда бот шлёт посты и где ждёт /stat (env + app_kv)."""
from __future__ import annotations

import asyncio
import sys


async def _run() -> int:
    from wheel_bot.config import load_settings
    from wheel_bot.db import connect
    from wheel_bot.destinations import read_destination_config

    settings = load_settings()
    conn = await connect(settings.database_path)
    try:
        cfg = await read_destination_config(conn, settings)
    finally:
        await conn.close()

    print("=== Из .env ===")
    print("TARGET_CHAT_ID (stats):", cfg["env_stats_chat_id"])
    print("WHEEL_CHANNEL_ID:      ", cfg["env_channel_chat_id"])
    print()
    print("=== app_kv (WebApp «Админ», если сохраняли) ===")
    print("cfg_stats_chat_id:     ", cfg["kv_stats_chat_id"] or "(нет, берётся из .env)")
    print("cfg_wheel_channel_id:  ", cfg["kv_channel_chat_id"] or "(нет, берётся из .env)")
    print()
    print("=== Фактически используется ===")
    print("Чат для /stat и БД:    ", cfg["stats_chat_id"])
    print("Канал (настроен):      ", cfg["channel_chat_id"])
    print("Режим постов:          ", cfg["post_target"])
    print("Куда уходят посты:     ", cfg["post_chat_id"])
    print()
    if cfg["channel_chat_id"] is not None and int(cfg["stats_chat_id"]) == int(cfg["channel_chat_id"]):
        print("ОШИБКА: ID чата и канала совпадают — исправьте WebApp или .env")
        return 1
    if int(cfg["stats_chat_id"]) != int(cfg["env_stats_chat_id"]):
        print("Внимание: stats_chat_id переопределён в app_kv (не совпадает с .env)")
    return 0


def main() -> int:
    try:
        return asyncio.run(_run())
    except Exception:
        import traceback

        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
