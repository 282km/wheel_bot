#!/usr/bin/env python3
"""
Удалить одно колесо из истории (session_id).

Примеры:
  python scripts/delete_wheel_session.py --list
  python scripts/delete_wheel_session.py --id 2
  python scripts/delete_wheel_session.py --second   # колесо с id=2 (второе по порядку)
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wheel_bot.config import load_settings  # noqa: E402
from wheel_bot.db import connect, delete_wheel_session, list_wheel_history  # noqa: E402


def _default_db_path() -> Path:
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "app.db"


async def _list_sessions(db_path: Path, chat_id: int) -> int:
    conn = await connect(db_path)
    try:
        items = await list_wheel_history(conn, chat_id, limit=50)
        if not items:
            print("История колёс пуста.")
            return 0
        print(f"Чат TARGET_CHAT_ID={chat_id}\n")
        for it in reversed(items):
            print(
                f"  id={it['id']}  {it['created_at']}  "
                f"занёс={it['depositor_nick']}  ${it['deposit_amount']}  "
                f"победителей={it['winners_count']}  фонд=${it['prizes_sum']}"
            )
        return 0
    finally:
        await conn.close()


async def _delete(db_path: Path, session_id: int) -> int:
    conn = await connect(db_path)
    try:
        ok = await delete_wheel_session(conn, session_id)
        if not ok:
            print(f"Колесо id={session_id} не найдено.", file=sys.stderr)
            return 1
        print(f"Удалено колесо id={session_id} (roster и раунды — каскадом).")
        return 0
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Удалить одно колесо из SQLite")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="Показать историю колёс")
    parser.add_argument("--id", type=int, default=None, help="ID колеса (wheel_sessions.id)")
    parser.add_argument(
        "--second",
        action="store_true",
        help="Удалить колесо с id=2 (второе по счёту в базе)",
    )
    parser.add_argument("--yes", action="store_true", help="Без подтверждения")
    args = parser.parse_args()

    db_path = args.db or _default_db_path()
    if not db_path.is_file():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    try:
        settings = load_settings()
        chat_id = int(settings.target_chat_id)
    except Exception as e:
        print(f"Не удалось прочитать .env: {e}", file=sys.stderr)
        return 1

    if args.list:
        return asyncio.run(_list_sessions(db_path, chat_id))

    session_id = args.id
    if args.second:
        session_id = 2
    if session_id is None:
        parser.print_help()
        return 1

    print(f"База: {db_path}")
    print(f"Будет удалено колесо id={session_id}")
    if not args.yes:
        answer = input("Продолжить? Введите yes: ").strip().lower()
        if answer not in ("yes", "y", "да"):
            print("Отменено.")
            return 0

    return asyncio.run(_delete(db_path, session_id))


if __name__ == "__main__":
    raise SystemExit(main())
