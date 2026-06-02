#!/usr/bin/env python3
"""Записать в app_kv ID чата и канала из .env (если в WebApp сохранили не те значения)."""
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
    from wheel_bot.posting import set_configured_chat_ids

    settings = load_settings()
    conn = await connect(settings.database_path)
    try:
        before = await read_destination_config(conn, settings)
        print("Before:", before["stats_chat_id"], before["channel_chat_id"])
        await set_configured_chat_ids(
            conn,
            int(settings.target_chat_id),
            settings.wheel_channel_id,
        )
        after = await read_destination_config(conn, settings)
        print("After: ", after["stats_chat_id"], after["channel_chat_id"])
    finally:
        await conn.close()
    print("Done. Restart not required.")
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
