#!/usr/bin/env python3
"""Разово отправить в чат статистики число побед участника в колёсах за всю историю."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fmt_money(x: float) -> str:
    s = f"{x:g}"
    return f"${s.replace('.', ',')}"


async def _query(conn, chat_id: int, nick_query: str) -> dict[str, Any] | None:
    row = await (
        await conn.execute(
            """
            SELECT id, poker_nick, description, is_hidden
            FROM participants
            WHERE poker_nick LIKE ? COLLATE NOCASE
            ORDER BY poker_nick COLLATE NOCASE ASC
            LIMIT 1
            """,
            (f"%{nick_query.strip()}%",),
        )
    ).fetchone()
    if not row:
        return None

    pid = int(row["id"])
    nick = str(row["poker_nick"])
    desc = str(row["description"] or "").strip()
    label = f"{nick} ({desc})" if desc else nick

    wins_row = await (
        await conn.execute(
            """
            SELECT COUNT(DISTINCT s.id) AS wins
            FROM wheel_spins w
            JOIN wheel_sessions s ON s.id = w.session_id
            WHERE w.winner_id = ? AND s.chat_id = ?
            """,
            (pid, chat_id),
        )
    ).fetchone()
    total_row = await (
        await conn.execute(
            "SELECT COUNT(*) AS c FROM wheel_sessions WHERE chat_id = ?",
            (chat_id,),
        )
    ).fetchone()
    money_row = await (
        await conn.execute(
            """
            SELECT COALESCE(SUM(w.prize_amount), 0) AS s
            FROM wheel_spins w
            JOIN wheel_sessions s ON s.id = w.session_id
            WHERE w.winner_id = ? AND s.chat_id = ?
            """,
            (pid, chat_id),
        )
    ).fetchone()

    return {
        "label": label,
        "is_hidden": bool(int(row["is_hidden"])),
        "wins": int(wins_row["wins"]) if wins_row else 0,
        "total_wheels": int(total_row["c"]) if total_row else 0,
        "won_sum": float(money_row["s"]) if money_row else 0.0,
    }


def _format_block(data: dict[str, Any]) -> str:
    lines = [
        f"🎡 {data['label']}",
        "",
        f"📊 Побед в колёсах за всю историю: {data['wins']} из {data['total_wheels']}",
        f"💵 Выиграно: {_fmt_money(float(data['won_sum']))}",
    ]
    if data.get("is_hidden"):
        lines.append("")
        lines.append("⚠️ Участник сейчас скрыт в списке.")
    return "\n".join(lines)


def _send_message(token: str, chat_id: int, text: str) -> dict:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


async def _run(args: argparse.Namespace) -> int:
    from wheel_bot.config import load_settings
    from wheel_bot.db import connect
    from wheel_bot.posting import get_stats_chat_id

    settings = load_settings()
    chat_id = int(args.chat_id) if args.chat_id is not None else get_stats_chat_id(settings)
    conn = await connect(settings.database_path)
    try:
        info = await _query(conn, chat_id, args.nick)
    finally:
        await conn.close()

    if info is None:
        print(f"Участник не найден: {args.nick!r}", file=sys.stderr)
        return 1

    text = _format_block(info)
    print(text)
    if args.dry_run:
        print("\n(dry-run: сообщение не отправлено)")
        return 0

    try:
        result = _send_message(settings.bot_token, chat_id, text)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(f"Telegram API error: HTTP {exc.code} {body}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Telegram API error: {exc}", file=sys.stderr)
        return 1

    if not result.get("ok"):
        print(f"Telegram API error: {result}", file=sys.stderr)
        return 1

    print(f"\nOK: отправлено в chat_id={chat_id}")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Post participant wheel wins to stats chat.")
    parser.add_argument("nick", nargs="?", default="Регуляр кэшбеков", help="Фрагмент poker_nick")
    parser.add_argument("--chat-id", type=int, default=None, help="Override TARGET_CHAT_ID")
    parser.add_argument("--dry-run", action="store_true", help="Only print message, do not send")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()
