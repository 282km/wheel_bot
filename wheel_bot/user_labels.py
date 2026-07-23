from __future__ import annotations

import re
from typing import Any

import aiosqlite

from wheel_bot import db

_USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,32}$")


def telegram_display_name(user: Any) -> str:
    """Имя для сообщений в чате (игра, бонус)."""
    if not user:
        return "Игрок"
    parts: list[str] = []
    first = getattr(user, "first_name", None)
    last = getattr(user, "last_name", None)
    if first:
        parts.append(str(first).strip())
    if last:
        parts.append(str(last).strip())
    name = " ".join(p for p in parts if p)
    if name:
        return name
    username = getattr(user, "username", None)
    if username:
        return str(username).lstrip("@")
    return "Игрок"


def plain_player_label(label: str, *, default: str = "Игрок") -> str:
    s = str(label or "").strip() or default
    return s[1:] if s.startswith("@") else s


def escape_markdown(text: str) -> str:
    """Экранирование для Telegram Markdown (legacy)."""
    out = str(text)
    for ch in ("\\", "_", "*", "`"):
        out = out.replace(ch, f"\\{ch}")
    return out


def is_likely_username(label: str) -> bool:
    s = plain_player_label(label, default="")
    if not s:
        return True
    if " " in s:
        return False
    if any("\u0400" <= c <= "\u04FF" for c in s):
        return False
    return bool(_USERNAME_RE.fullmatch(s))


def label_for_stats(label: str, *, fallback: str = "Игрок") -> str:
    s = plain_player_label(label, default="")
    if not s or is_likely_username(s):
        return fallback
    return s


async def remember_telegram_user(conn: aiosqlite.Connection, user: Any) -> str:
    label = telegram_display_name(user)
    tid = int(getattr(user, "id", 0) or 0)
    if not tid:
        return label

    first = getattr(user, "first_name", None)
    last = getattr(user, "last_name", None)
    has_real_name = bool(str(first or "").strip()) or bool(str(last or "").strip())
    if has_real_name:
        await db.upsert_user_display_name(conn, tid, label)
    return label


async def resolve_player_label(
    conn: aiosqlite.Connection,
    telegram_id: int,
    *,
    fallback: str = "Игрок",
) -> str:
    tid = int(telegram_id)
    row = await (
        await conn.execute(
            "SELECT display_name FROM users WHERE telegram_id = ?",
            (tid,),
        )
    ).fetchone()
    if row and str(row["display_name"] or "").strip():
        name = label_for_stats(str(row["display_name"]), fallback=fallback)
        if name != fallback:
            return name

    grow = await (
        await conn.execute(
            """
            SELECT user_label FROM game_plays
            WHERE telegram_id = ?
            ORDER BY played_at DESC
            LIMIT 1
            """,
            (tid,),
        )
    ).fetchone()
    if grow and str(grow["user_label"] or "").strip():
        name = label_for_stats(str(grow["user_label"]), fallback=fallback)
        if name != fallback:
            return name

    brow = await (
        await conn.execute(
            """
            SELECT user_label FROM bonus_wins
            WHERE telegram_id = ?
            ORDER BY won_at DESC
            LIMIT 1
            """,
            (tid,),
        )
    ).fetchone()
    if brow and str(brow["user_label"] or "").strip():
        name = label_for_stats(str(brow["user_label"]), fallback=fallback)
        if name != fallback:
            return name

    return fallback
