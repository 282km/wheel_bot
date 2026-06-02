#!/usr/bin/env python3
"""Проверка, что бот стартует после деплоя. Запуск: python scripts/verify_startup.py"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    print("Python:", sys.version.split()[0])
    try:
        from wheel_bot.config import _PROJECT_ROOT, load_settings
        from wheel_bot import db, spin_service  # noqa: F401
        from wheel_bot.api import create_app  # noqa: F401
        from wheel_bot.render_wheel import render_multi_round_spin_media  # noqa: F401

        print("Project root:", _PROJECT_ROOT)
        print("Env file:", _PROJECT_ROOT / ".env", "exists:", (_PROJECT_ROOT / ".env").is_file())
        settings = load_settings()
        print("TARGET_CHAT_ID:", settings.target_chat_id)
        print("PUBLIC_BASE_URL:", settings.public_base_url)

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
