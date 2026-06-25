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
    top_bonus = await _top_bonus_winners(conn, _date_clause_for_won_at(params_date), params_date)

    return {
        "period": period,
        "wheels_count": wheels,
        "prizes_sum": prizes_sum,
        "top_allocated": top_allocated,
        "top_win_amounts": top_win_sum,
        "top_win_counts": top_win_cnt,
        "top_bonus_winners": top_bonus,
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


def _date_clause_for_won_at(params_date: list[Any]) -> str:
    parts: list[str] = []
    if len(params_date) >= 1:
        parts.append(" AND datetime(won_at) >= datetime(?)")
    if len(params_date) >= 2:
        parts.append(" AND datetime(won_at) < datetime(?)")
    return "".join(parts)


async def _top_bonus_winners(
    conn: aiosqlite.Connection,
    date_clause: str,
    params_date: list[Any],
) -> list[dict[str, Any]]:
    """Кто поймал /bonus за период: только с хотя бы одним выигрышем."""
    sql = f"""
    SELECT
        telegram_id,
        COALESCE(NULLIF(MAX(user_label), ''), 'Участник') AS label,
        COUNT(*) AS wins,
        SUM(amount) AS total
    FROM bonus_wins
    WHERE 1=1{date_clause}
    GROUP BY telegram_id
    HAVING wins > 0
    ORDER BY wins DESC, total DESC, label COLLATE NOCASE ASC
    """
    cur = await conn.execute(sql, params_date)
    rows = await cur.fetchall()
    return [
        {
            "label": str(r["label"]),
            "wins": int(r["wins"]),
            "total": float(r["total"]),
        }
        for r in rows
    ]


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


async def _total_wheels_count(conn: aiosqlite.Connection, chat_id: int) -> int:
    row = await (
        await conn.execute(
            "SELECT COUNT(*) AS c FROM wheel_sessions WHERE chat_id = ?",
            (chat_id,),
        )
    ).fetchone()
    return int(row["c"]) if row else 0


async def losers_summary(conn: aiosqlite.Connection, chat_id: int) -> dict[str, Any]:
    """Топ лузеров за всю историю среди активных (не скрытых) участников."""
    total_wheels = await _total_wheels_count(conn, chat_id)
    prizes_sum = await _total_prizes_sum(conn, chat_id, "", [])

    win_sql = """
    SELECT p.poker_nick AS nick, p.description AS description, COUNT(DISTINCT s.id) AS wins
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
            "total_wheels": total_wheels,
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
        "total_wheels": total_wheels,
        "prizes_sum": prizes_sum,
        "worst_wins": worst_wins,
        "worst_money": worst_money,
    }


async def participant_wheel_wins(conn: aiosqlite.Connection, chat_id: int, nick_query: str) -> dict[str, Any] | None:
    """Победы участника в колёсах за всю историю (по poker_nick, фрагмент)."""
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
    total_wheels = await _total_wheels_count(conn, chat_id)

    nick = str(row["poker_nick"])
    desc = str(row["description"] or "").strip()
    return {
        "id": pid,
        "nick": nick,
        "label": _label(nick, desc),
        "is_hidden": bool(int(row["is_hidden"])),
        "wins": int(wins_row["wins"]) if wins_row else 0,
        "total_wheels": total_wheels,
        "won_sum": float(money_row["s"]) if money_row else 0.0,
    }
