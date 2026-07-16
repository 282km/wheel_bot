from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Literal, Optional
from wheel_bot.timezones import get_timezone

import aiosqlite

from wheel_bot import db
from wheel_bot.db import utc_now_iso

BONUS_COOLDOWN = timedelta(hours=24)
BONUS_WIN_ODDS = 500
BONUS_AMOUNT_MIN = 3
BONUS_AMOUNT_MAX = 5

# Разовая акция: в день первого запуска первый успешный /bonus выигрывает гарантированно.
KV_BONUS_GUARANTEED_PROMO_DATE = "bonus_guaranteed_promo_date"
KV_BONUS_GUARANTEED_PROMO_CONSUMED = "bonus_guaranteed_promo_consumed"
BONUS_TIMEZONE = "Europe/Moscow"

BonusStatus = Literal["cooldown", "lose", "win"]


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


async def get_last_bonus_attempt(conn: aiosqlite.Connection, telegram_id: int) -> Optional[datetime]:
    row = await (
        await conn.execute(
            "SELECT last_attempt_at FROM bonus_attempts WHERE telegram_id = ?",
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    return _parse_iso(str(row["last_attempt_at"]))


async def record_bonus_attempt(
    conn: aiosqlite.Connection,
    telegram_id: int,
    *,
    won: bool,
    amount: Optional[float],
) -> None:
    now = utc_now_iso()
    await conn.execute(
        """
        INSERT INTO bonus_attempts (telegram_id, last_attempt_at, last_won, last_amount)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            last_attempt_at = excluded.last_attempt_at,
            last_won = excluded.last_won,
            last_amount = excluded.last_amount
        """,
        (int(telegram_id), now, 1 if won else 0, amount),
    )
    await conn.commit()


def format_bonus_wait(seconds: int) -> str:
    seconds = max(0, int(seconds))
    hours, rem = divmod(seconds, 3600)
    minutes, _ = divmod(rem, 60)
    if hours and minutes:
        return f"{hours} ч {minutes} мин"
    if hours:
        return f"{hours} ч"
    if minutes:
        return f"{minutes} мин"
    return "меньше минуты"


def _bonus_today_key() -> str:
    return datetime.now(get_timezone(BONUS_TIMEZONE)).date().isoformat()


async def _consume_guaranteed_promo(conn: aiosqlite.Connection, today: str) -> None:
    await db.set_kv(conn, KV_BONUS_GUARANTEED_PROMO_DATE, today)
    await db.set_kv(conn, KV_BONUS_GUARANTEED_PROMO_CONSUMED, "1")


async def _take_guaranteed_promo_win(conn: aiosqlite.Connection) -> bool:
    """Первый /bonus в promo-день выигрывает гарантированно; дальше — обычные шансы."""
    today = _bonus_today_key()
    promo_date = (await db.get_kv(conn, KV_BONUS_GUARANTEED_PROMO_DATE, "") or "").strip()
    consumed = (await db.get_kv(conn, KV_BONUS_GUARANTEED_PROMO_CONSUMED, "") or "").strip()

    if promo_date and promo_date != today:
        return False
    if consumed == "1":
        return False

    await _consume_guaranteed_promo(conn, today)
    return True


async def record_bonus_win(
    conn: aiosqlite.Connection,
    telegram_id: int,
    user_label: str,
    amount: float,
) -> None:
    await conn.execute(
        """
        INSERT INTO bonus_wins (telegram_id, user_label, amount, won_at)
        VALUES (?, ?, ?, ?)
        """,
        (int(telegram_id), str(user_label or "").strip(), float(amount), utc_now_iso()),
    )
    await conn.commit()


async def try_daily_bonus(
    conn: aiosqlite.Connection,
    telegram_id: int,
    *,
    user_label: str = "",
) -> dict[str, Any]:
    """Одна попытка /bonus: не чаще раза в сутки, шанс 1 к 500."""
    last = await get_last_bonus_attempt(conn, telegram_id)
    now = datetime.now(timezone.utc)
    if last is not None:
        elapsed = now - last
        if elapsed < BONUS_COOLDOWN:
            wait_seconds = int((BONUS_COOLDOWN - elapsed).total_seconds())
            return {"status": "cooldown", "wait_seconds": wait_seconds}

    won = await _take_guaranteed_promo_win(conn)
    if not won:
        won = secrets.randbelow(BONUS_WIN_ODDS) == 0
    amount: Optional[float] = None
    if won:
        amount = float(secrets.randbelow(BONUS_AMOUNT_MAX - BONUS_AMOUNT_MIN + 1) + BONUS_AMOUNT_MIN)

    await record_bonus_attempt(conn, telegram_id, won=won, amount=amount)
    if won:
        assert amount is not None
        await record_bonus_win(conn, telegram_id, user_label, amount)
        return {"status": "win", "amount": amount}
    return {"status": "lose"}
