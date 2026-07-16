#!/usr/bin/env python3
"""Smoke-test утреннего дайджеста (RSS + шаблон). Запуск на сервере:
  .venv/bin/python scripts/test_morning_digest.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def main() -> int:
    from wheel_bot.config import load_settings
    from wheel_bot.db import connect
    from wheel_bot.morning_digest import prepare_morning_digest_post

    settings = load_settings()
    conn = await connect(settings.database_path)
    try:
        post = await prepare_morning_digest_post(conn, settings)
    finally:
        await conn.close()

    print("mode:", post.source_mode)
    print("title:", post.news_title or "(historical)")
    print("image:", post.image_url or "(none)")
    print("text_len:", len(post.text))
    print("---")
    print(post.text)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
