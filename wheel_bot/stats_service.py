from __future__ import annotations

from typing import Any, Optional

import aiosqlite

from wheel_bot.periods import PeriodKey, resolve_period


def _label(nick: str, desc: Optional[str]) -> str:
    d = str(desc or "").strip()
    return f"{nick} ({d})" if d else str(nick)


async def stats_summary(conn: aiosqlite.Connection, chat_id: int, period: PeriodKey) -> dict[str, Any]:
    pr = resolve_period(period)
    date_clause_ws = ""
    params_date: list[Any] = []
    if pr.start_iso is not None:
        date_clause_ws += " AND datetime(created_at) >= datetime(?)"
        params_date.append(pr.start_iso)
    if pr.end_iso is not None:
        date_clause_ws += " AND datetime(created_at) < datetime(?)"
        params_date.append(pr.end_iso)

    params_main: list[Any] = [chat_id, *params_date]

    row = await (
        await conn.execute(
            f"SELECT COUNT(*) AS c FROM wheel_sessions WHERE chat_id = ?{date_clause_ws}",
            params_main,
        )
    ).fetchone()
    wheels = int(row["c"])
    prizes_sum = await _total_prizes_sum(conn, chat_id, _date_clause_for_alias(params_date, "s"), params_date)

    top_allocated = await _top_allocated_by_depositor(
        conn, chat_id, _date_clause_for_alias(params_date, "s"), params_date
    )
    top_win_sum = await _top_win_sum(conn, chat_id, _date_clause_for_alias(params_date, "s"), params_date)
    top_win_cnt = await _top_win_cnt(conn, chat_id, _date_clause_for_alias(params_date, "s"), params_date)

    return {
        "period": period,
        "wheels_count": wheels,
        "prizes_sum": prizes_sum,
        "top_allocated": top_allocated,
        "top_win_amounts": top_win_sum,
        "top_win_counts": top_win_cnt,
    }


async def _top_win_sum(conn: aiosqlite.Connection, chat_id: int, date_clause: str, params_date: list[Any]) -> list[dict[str, Any]]:
    sql = f"""
    SELECT p.poker_nick AS nick, p.description AS description, SUM(w.prize_amount) AS total
    FROM wheel_spins w
    JOIN wheel_sessions s ON s.id = w.session_id
    JOIN participants p ON p.id = w.winner_id
    WHERE s.chat_id = ?{date_clause}
    GROUP BY p.id
    ORDER BY total DESC
    LIMIT 5
    """
    params: list[Any] = [chat_id, *params_date]
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [{"nick": _label(str(r["nick"]), r["description"]), "amount": float(r["total"])} for r in rows]


async def _top_win_cnt(conn: aiosqlite.Connection, chat_id: int, date_clause: str, params_date: list[Any]) -> list[dict[str, Any]]:
    sql = f"""
    SELECT p.poker_nick AS nick, p.description AS description, COUNT(*) AS cnt
    FROM wheel_spins w
    JOIN wheel_sessions s ON s.id = w.session_id
    JOIN participants p ON p.id = w.winner_id
    WHERE s.chat_id = ?{date_clause}
    GROUP BY p.id
    ORDER BY cnt DESC
    LIMIT 5
    """
    params: list[Any] = [chat_id, *params_date]
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [{"nick": _label(str(r["nick"]), r["description"]), "wins": int(r["cnt"])} for r in rows]


async def _top_allocated_by_depositor(
    conn: aiosqlite.Connection, chat_id: int, date_clause: str, params_date: list[Any]
) -> list[dict[str, Any]]:
    """Сумма призов по колёсам, которые игрок выделил как «кто занёс»."""
    sql = f"""
    SELECT p.poker_nick AS nick, p.description AS description, COALESCE(SUM(w.prize_amount), 0) AS total
    FROM wheel_sessions s
    JOIN participants p ON p.id = s.depositor_id
    LEFT JOIN wheel_spins w ON w.session_id = s.id
    WHERE s.chat_id = ?{date_clause}
    GROUP BY p.id
    ORDER BY total DESC
    LIMIT 5
    """
    params: list[Any] = [chat_id, *params_date]
    cur = await conn.execute(sql, params)
    rows = await cur.fetchall()
    return [{"nick": _label(str(r["nick"]), r["description"]), "amount": float(r["total"])} for r in rows]


def _date_clause_for_alias(params_date: list[Any], alias: str) -> str:
    parts: list[str] = []
    if len(params_date) >= 1:
        parts.append(f" AND datetime({alias}.created_at) >= datetime(?)")
    if len(params_date) >= 2:
        parts.append(f" AND datetime({alias}.created_at) < datetime(?)")
    return "".join(parts)


async def _total_prizes_sum(conn: aiosqlite.Connection, chat_id: int, date_clause: str, params_date: list[Any]) -> float:
    row = await (
        await conn.execute(
            f"""
            SELECT COALESCE(SUM(w.prize_amount), 0) AS s
            FROM wheel_spins w
            JOIN wheel_sessions s ON s.id = w.session_id
            WHERE s.chat_id = ?{date_clause}
            """,
            [chat_id, *params_date],
        )
    ).fetchone()
    return float(row["s"]) if row else 0.0


async def _total_spins_count(conn: aiosqlite.Connection, chat_id: int) -> int:
    row = await (
        await conn.execute(
            """
            SELECT COUNT(*) AS c
            FROM wheel_spins w
            JOIN wheel_sessions s ON s.id = w.session_id
            WHERE s.chat_id = ?
            """,
            (chat_id,),
        )
    ).fetchone()
    return int(row["c"]) if row else 0


async def losers_summary(conn: aiosqlite.Connection, chat_id: int) -> dict[str, Any]:
    """Топ лузеров за всю историю среди активных (не скрытых) участников."""
    total_spins = await _total_spins_count(conn, chat_id)
    prizes_sum = await _total_prizes_sum(conn, chat_id, "", [])

    win_sql = """
    SELECT p.poker_nick AS nick, p.description AS description, COUNT(w.id) AS wins
    FROM participants p
    LEFT JOIN wheel_spins w ON w.winner_id = p.id
    LEFT JOIN wheel_sessions s ON s.id = w.session_id AND s.chat_id = ?
    WHERE p.is_hidden = 0
    GROUP BY p.id
    ORDER BY wins ASC, p.poker_nick COLLATE NOCASE ASC
    LIMIT 10
    """
    cur = await conn.execute(win_sql, (chat_id,))
    worst_wins = [
        {
            "nick": _label(str(r["nick"]), r["description"]),
            "wins": int(r["wins"]),
            "total_spins": total_spins,
        }
        for r in await cur.fetchall()
    ]

    money_sql = """
    SELECT p.poker_nick AS nick, p.description AS description, COALESCE(SUM(w.prize_amount), 0) AS total
    FROM participants p
    LEFT JOIN wheel_spins w ON w.winner_id = p.id
    LEFT JOIN wheel_sessions s ON s.id = w.session_id AND s.chat_id = ?
    WHERE p.is_hidden = 0
    GROUP BY p.id
    ORDER BY total ASC, p.poker_nick COLLATE NOCASE ASC
    LIMIT 10
    """
    cur = await conn.execute(money_sql, (chat_id,))
    worst_money = [
        {
            "nick": _label(str(r["nick"]), r["description"]),
            "amount": float(r["total"]),
            "prizes_sum": prizes_sum,
        }
        for r in await cur.fetchall()
    ]

    return {
        "total_spins": total_spins,
        "prizes_sum": prizes_sum,
        "worst_wins": worst_wins,
        "worst_money": worst_money,
    }
