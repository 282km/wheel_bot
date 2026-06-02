#!/usr/bin/env python3
"""
Импорт участников из deploy/participants_seed.json в SQLite.

Пример (на сервере, бот лучше остановить на секунду):
  cd /opt/wheel_bot_git
  source .venv/bin/activate
  python scripts/import_participants.py
  python scripts/import_participants.py --update   # обновить описания у существующих
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from wheel_bot.db import connect, insert_participant, list_participants, update_participant  # noqa: E402

DEFAULT_SEED = ROOT / "deploy" / "participants_seed.json"


def _default_db_path() -> Path:
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "app.db"


def _load_seed(path: Path) -> list[dict[str, str]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    out: list[dict[str, str]] = []
    for item in raw:
        nick = str(item.get("poker_nick") or "").strip()
        desc = str(item.get("description") or "").strip()
        if not nick:
            continue
        out.append({"poker_nick": nick, "description": desc})
    return out


async def _run(db_path: Path, seed_path: Path, update_existing: bool) -> dict[str, int]:
    items = _load_seed(seed_path)
    conn = await connect(db_path)
    try:
        existing = {p.poker_nick.lower(): p for p in await list_participants(conn)}
        stats = {"added": 0, "updated": 0, "skipped": 0}
        for item in items:
            nick = item["poker_nick"]
            desc = item["description"]
            key = nick.lower()
            row = existing.get(key)
            if row is None:
                await insert_participant(conn, nick, desc)
                stats["added"] += 1
                print(f"+ {nick}")
                continue
            if update_existing and row.description != desc:
                await update_participant(conn, row.id, poker_nick=None, description=desc)
                stats["updated"] += 1
                print(f"~ {nick}")
            else:
                stats["skipped"] += 1
                print(f"= {nick} (уже есть)")
        return stats
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт участников из JSON")
    parser.add_argument("--db", type=Path, default=None, help="Путь к app.db")
    parser.add_argument("--seed", type=Path, default=DEFAULT_SEED, help="JSON со списком")
    parser.add_argument(
        "--update",
        action="store_true",
        help="Обновить description у уже существующих ников",
    )
    args = parser.parse_args()
    db_path = args.db or _default_db_path()
    if not args.seed.is_file():
        print(f"Нет файла: {args.seed}", file=sys.stderr)
        return 1
    if not db_path.is_file():
        print(f"Нет базы: {db_path}", file=sys.stderr)
        return 1
    stats = asyncio.run(_run(db_path, args.seed, args.update))
    print(
        f"\nГотово: добавлено {stats['added']}, обновлено {stats['updated']}, "
        f"пропущено {stats['skipped']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
