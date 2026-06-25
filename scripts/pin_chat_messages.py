#!/usr/bin/env python3
"""Закрепить сообщения в чате статистики (TARGET_CHAT_ID) по message_id."""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _pin_message(token: str, chat_id: int, message_id: int, *, disable_notification: bool) -> dict:
    url = f"https://api.telegram.org/bot{token}/pinChatMessage"
    payload = json.dumps(
        {
            "chat_id": chat_id,
            "message_id": message_id,
            "disable_notification": disable_notification,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Pin messages in stats chat via Bot API.")
    parser.add_argument("message_ids", type=int, nargs="+", help="Telegram message_id values")
    parser.add_argument("--chat-id", type=int, default=None, help="Override TARGET_CHAT_ID from .env")
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send pin notification to chat (default: silent pin)",
    )
    args = parser.parse_args()

    from wheel_bot.config import load_settings

    settings = load_settings()
    chat_id = int(args.chat_id if args.chat_id is not None else settings.target_chat_id)
    disable_notification = not args.notify

    ok = 0
    for message_id in args.message_ids:
        try:
            result = _pin_message(
                settings.bot_token,
                chat_id,
                int(message_id),
                disable_notification=disable_notification,
            )
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            print(f"FAIL message_id={message_id}: HTTP {exc.code} {body}", file=sys.stderr)
            continue
        except urllib.error.URLError as exc:
            print(f"FAIL message_id={message_id}: {exc}", file=sys.stderr)
            continue

        if result.get("ok"):
            print(f"OK pinned message_id={message_id} in chat_id={chat_id}")
            ok += 1
        else:
            print(
                f"FAIL message_id={message_id}: {result.get('error_code')} {result.get('description')}",
                file=sys.stderr,
            )

    return 0 if ok == len(args.message_ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
