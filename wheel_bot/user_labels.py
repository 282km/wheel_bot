from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from wheel_bot import db


def telegram_display_name(user: Any) -> str:
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


async def remember_telegram_user(conn: aiosqlite.Connection, user: Any) -> str:
    label = telegram_display_name(user)
    tid = int(getattr(user, "id", 0) or 0)
    if tid:
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
        return plain_player_label(str(row["display_name"]), default=fallback)

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
        return plain_player_label(str(grow["user_label"]), default=fallback)

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
        return plain_player_label(str(brow["user_label"]), default=fallback)

    return fallback
