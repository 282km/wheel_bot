#!/usr/bin/env python3
"""Проверка, что бот стартует после деплоя. Запуск: python scripts/verify_startup.py"""
from __future__ import annotations

import sys
import traceback


def main() -> int:
    print("Python:", sys.version.split()[0])
    try:
        from wheel_bot import config, db, spin_service  # noqa: F401
        from wheel_bot.api import create_app  # noqa: F401
        from wheel_bot.render_wheel import render_multi_round_spin_media  # noqa: F401

        roster = [("Alice", "", 0), ("Bob", "", 120), ("Carol", "", 240)]
        data, ext = render_multi_round_spin_media([(roster, 1, "Bob")], spin_sec=0.5, hold_sec=0.3)
        print(f"render ok: {len(data)} bytes ({ext})")
        print("ALL OK")
        return 0
    except Exception:
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
