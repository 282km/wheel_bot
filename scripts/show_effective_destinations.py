#!/usr/bin/env python3
"""Показать, куда бот шлёт посты и где ждёт /stat (только .env)."""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


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

    print("=== Из .env (единственный источник ID) ===")
    print("TARGET_CHAT_ID (stats):", settings.target_chat_id)
    print("WHEEL_CHANNEL_ID:      ", settings.wheel_channel_id)
    print()
    print("=== Фактически используется ===")
    print("Чат для /stat и БД:    ", cfg["stats_chat_id"])
    print("Канал (настроен):      ", cfg["channel_chat_id"])
    print("Режим постов:          ", cfg["post_target"])
    print("Куда уходят посты:     ", cfg["post_chat_id"])
    print()
    if cfg["channel_chat_id"] is not None and int(cfg["stats_chat_id"]) == int(cfg["channel_chat_id"]):
        print("ОШИБКА: TARGET_CHAT_ID и WHEEL_CHANNEL_ID совпадают в .env")
        return 1
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
