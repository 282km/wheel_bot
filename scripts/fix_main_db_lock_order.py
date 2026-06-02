#!/usr/bin/env python3
"""Исправить порядок db_lock в wheel_bot/main.py (UnboundLocalError при старте)."""
from __future__ import annotations

import re
import sys
from pathlib import Path


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    path = root / "wheel_bot" / "main.py"
    text = path.read_text(encoding="utf-8")
    if "db_lock = asyncio.Lock()" in text and text.index("db_lock = asyncio.Lock()") < text.index(
        "dp.include_router(setup_router"
    ):
        print("main.py already OK:", path)
        return 0

    pattern = re.compile(
        r"(\n        bot = Bot\(settings\.bot_token\)\n"
        r"        dp = Dispatcher\(\)\n"
        r"        dp\.include_router\(setup_router\(settings, conn, db_lock\)\)\n)"
        r"(\n        db_lock = asyncio\.Lock\(\)\n"
        r"        app = create_app\(settings, conn, bot, dp, db_lock\))",
        re.MULTILINE,
    )
    repl = (
        "\n        db_lock = asyncio.Lock()\n"
        "        bot = Bot(settings.bot_token)\n"
        "        dp = Dispatcher()\n"
        "        dp.include_router(setup_router(settings, conn, db_lock))\n"
        "        app = create_app(settings, conn, bot, dp, db_lock)"
    )
    new_text, n = pattern.subn(repl, text, count=1)
    if n != 1:
        print("Pattern not found in", path, file=sys.stderr)
        print("Edit manually: db_lock = asyncio.Lock() BEFORE dp.include_router(...)", file=sys.stderr)
        return 1
    path.write_text(new_text, encoding="utf-8")
    print("Fixed:", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
