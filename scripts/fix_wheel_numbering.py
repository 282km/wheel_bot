#!/usr/bin/env python3
"""
Исправление нумерации колёс в SQLite.

Типовой сценарий (два реальных колеса, лишние #2 и #4):
  python scripts/fix_wheel_numbering.py --list
  sudo systemctl stop wheel-bot
  python scripts/fix_wheel_numbering.py --two-wheels-fix --yes
  sudo systemctl start wheel-bot

Вручную:
  python scripts/fix_wheel_numbering.py --delete 4,2 --move 3:2 --yes
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
from wheel_bot.db import (  # noqa: E402
    connect,
    delete_wheel_session,
    list_session_winners,
    list_wheel_history,
    renumber_wheel_session,
    sync_wheel_session_autoincrement,
)


def _default_db_path() -> Path:
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "app.db"


async def _print_sessions(db_path: Path, chat_id: int) -> None:
    conn = await connect(db_path)
    try:
        items = await list_wheel_history(conn, chat_id, limit=100)
        if not items:
            print("История колёс пуста.")
            return
        print(f"Чат TARGET_CHAT_ID={chat_id}\n")
        for it in sorted(items, key=lambda x: int(x["id"])):
            winners = await list_session_winners(conn, int(it["id"]))
            print(
                f"  id={it['id']}  {it['created_at']}  занёс={it['depositor_nick']}  "
                f"${it['deposit_amount']}  победителей={len(winners)}  фонд=${it['prizes_sum']}"
            )
    finally:
        await conn.close()


async def _peek_next_id(db_path: Path) -> int:
    conn = await connect(db_path)
    try:
        row = await (await conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM wheel_sessions")).fetchone()
        return int(row["m"]) + 1
    finally:
        await conn.close()


async def _apply(
    db_path: Path,
    *,
    delete_ids: list[int],
    moves: list[tuple[int, int]],
) -> int:
    conn = await connect(db_path)
    try:
        for sid in delete_ids:
            if await delete_wheel_session(conn, sid):
                print(f"Удалено колесо #{sid}")
            else:
                print(f"Пропуск: колесо #{sid} не найдено")

        for src, dst in moves:
            await renumber_wheel_session(conn, src, dst, replace_target=True)
            print(f"Перенумеровано: #{src} → #{dst}")

        nxt = await sync_wheel_session_autoincrement(conn)
        print(f"Готово. Следующее колесо получит id={nxt}")
        return 0
    finally:
        await conn.close()


def _parse_moves(raw: str) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            raise ValueError(f"Неверный формат move: {part} (нужно 3:2)")
        a, b = part.split(":", 1)
        out.append((int(a.strip()), int(b.strip())))
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Исправить нумерацию колёс в SQLite")
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--list", action="store_true", help="Показать колёса")
    parser.add_argument("--delete", type=str, default="", help="Удалить id через запятую, напр. 4,2")
    parser.add_argument("--move", type=str, default="", help="Перенос id, напр. 3:2")
    parser.add_argument(
        "--two-wheels-fix",
        action="store_true",
        help="Удалить #4 и #2, перенести #3→#2, выставить следующий id=3",
    )
    parser.add_argument("--yes", action="store_true")
    args = parser.parse_args()

    db_path = args.db or _default_db_path()
    if not db_path.is_file():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    if args.list:
        try:
            settings = load_settings()
            chat_id = int(settings.target_chat_id)
        except Exception as e:
            print(f"Не удалось прочитать .env: {e}", file=sys.stderr)
            return 1
        asyncio.run(_print_sessions(db_path, chat_id))
        nxt = asyncio.run(_peek_next_id(db_path))
        print(f"(сейчас следующий id будет ≈{nxt})")
        return 0

    delete_ids: list[int] = []
    moves: list[tuple[int, int]] = []
    if args.two_wheels_fix:
        delete_ids = [4, 2]
        moves = [(3, 2)]
    if args.delete.strip():
        delete_ids.extend(int(x.strip()) for x in args.delete.split(",") if x.strip())
    if args.move.strip():
        moves.extend(_parse_moves(args.move))

    if not delete_ids and not moves:
        parser.print_help()
        return 1

    print(f"База: {db_path}")
    print(f"Удалить: {delete_ids or '—'}")
    print(f"Перенумеровать: {moves or '—'}")
    if not args.yes:
        answer = input("Продолжить? Введите yes: ").strip().lower()
        if answer not in ("yes", "y", "да"):
            print("Отменено.")
            return 0

    return asyncio.run(_apply(db_path, delete_ids=delete_ids, moves=moves))


if __name__ == "__main__":
    raise SystemExit(main())
