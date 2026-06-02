#!/usr/bin/env python3
"""
Полный сброс данных колеса (участники, история, драфт, шаблоны).
Роли пользователей (admin/superadmin) сохраняются.

Перед запуском остановите бота:
  sudo systemctl stop wheel-bot

Пример:
  cd /opt/wheel_bot_git
  source .venv/bin/activate
  python scripts/reset_wheel_data.py --yes
  sudo systemctl start wheel-bot
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

from wheel_bot.db import connect, reset_all_wheel_data  # noqa: E402


def _default_db_path() -> Path:
    load_dotenv(ROOT / ".env")
    raw = os.getenv("DATABASE_PATH", "").strip()
    if raw:
        return Path(raw).expanduser()
    return ROOT / "data" / "app.db"


async def _run(db_path: Path) -> dict[str, int]:
    conn = await connect(db_path)
    try:
        return await reset_all_wheel_data(conn)
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Сброс всех данных колеса в SQLite")
    parser.add_argument("--db", type=Path, default=None, help="Путь к app.db (по умолчанию из .env)")
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Не спрашивать подтверждение",
    )
    args = parser.parse_args()
    db_path = args.db or _default_db_path()

    if not db_path.exists():
        print(f"База не найдена: {db_path}", file=sys.stderr)
        return 1

    print(f"База: {db_path}")
    print("Будут удалены: участники, все колёса/раунды, драфт, app_kv (шаблоны).")
    print("Сохранятся: users (admin/superadmin).")
    if not args.yes:
        answer = input("Продолжить? Введите yes: ").strip().lower()
        if answer not in ("yes", "y", "да"):
            print("Отменено.")
            return 0

    stats = asyncio.run(_run(db_path))
    print("Готово. Удалено записей:")
    for key, val in stats.items():
        print(f"  {key}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
