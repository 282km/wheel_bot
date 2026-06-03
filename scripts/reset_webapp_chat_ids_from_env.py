#!/usr/bin/env python3
"""Удалить устаревшие переопределения ID чата/канала из app_kv (теперь только .env)."""
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
    from wheel_bot.posting import clear_legacy_destination_kv

    settings = load_settings()
    conn = await connect(settings.database_path)
    try:
        removed = await clear_legacy_destination_kv(conn)
        print("Legacy app_kv keys removed:", removed)
        print("Use .env: TARGET_CHAT_ID=%s WHEEL_CHANNEL_ID=%s" % (
            settings.target_chat_id,
            settings.wheel_channel_id,
        ))
    finally:
        await conn.close()
    print("Done. Restart wheel-bot if it was running.")
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
