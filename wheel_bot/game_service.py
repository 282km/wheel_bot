from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

import aiosqlite

from wheel_bot.db import utc_now_iso
from wheel_bot.timezones import get_timezone
from wheel_bot.user_labels import resolve_player_label

GameType = Literal["blackjack", "mines"]

MINI_GAME_TYPES = ("blackjack", "mines")
MINI_GAME_TYPES_SQL = "('blackjack', 'mines')"


GAME_SCHEMA = """
CREATE TABLE IF NOT EXISTS game_plays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER NOT NULL,
    user_label TEXT NOT NULL DEFAULT '',
    game_type TEXT NOT NULL CHECK(game_type IN ('slots','bowling','duel')),
    dice_value INTEGER NOT NULL,
    points INTEGER NOT NULL,
    meta_json TEXT NOT NULL DEFAULT '{}',
    played_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_game_plays_played_at ON game_plays(played_at);
CREATE INDEX IF NOT EXISTS idx_game_plays_user_time ON game_plays(telegram_id, played_at);
"""


async def ensure_game_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(GAME_SCHEMA)
    await _migrate_game_plays(conn)
    from wheel_bot.blackjack_service import ensure_blackjack_schema
    from wheel_bot.mines_service import ensure_mines_schema

    await ensure_blackjack_schema(conn)
    await ensure_mines_schema(conn)
    await conn.commit()


async def _migrate_game_plays(conn: aiosqlite.Connection) -> None:
    row = await (
        await conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='game_plays'"
        )
    ).fetchone()
    if not row:
        return
    sql = str(row["sql"] or "")
    if "CHECK" not in sql.upper():
        return
    await conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS game_plays_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id INTEGER NOT NULL,
            user_label TEXT NOT NULL DEFAULT '',
            game_type TEXT NOT NULL,
            dice_value INTEGER NOT NULL DEFAULT 0,
            points INTEGER NOT NULL,
            meta_json TEXT NOT NULL DEFAULT '{}',
            played_at TEXT NOT NULL
        );
        INSERT INTO game_plays_new
            (id, telegram_id, user_label, game_type, dice_value, points, meta_json, played_at)
        SELECT id, telegram_id, user_label, game_type, dice_value, points, meta_json, played_at
        FROM game_plays;
        DROP TABLE game_plays;
        ALTER TABLE game_plays_new RENAME TO game_plays;
        CREATE INDEX IF NOT EXISTS idx_game_plays_played_at ON game_plays(played_at);
        CREATE INDEX IF NOT EXISTS idx_game_plays_user_time ON game_plays(telegram_id, played_at);
        """
    )


def week_bounds(when: Optional[datetime] = None, *, tz_name: str = "Europe/Moscow") -> tuple[datetime, datetime, str]:
    tz = get_timezone(tz_name)
    ref = (when or datetime.now(timezone.utc)).astimezone(tz)
    monday = ref.date() - timedelta(days=ref.weekday())
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = start + timedelta(days=7)
    sunday = monday + timedelta(days=6)
    label = f"{monday.strftime('%d.%m')}–{sunday.strftime('%d.%m.%Y')}"
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


async def record_play(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    game_type: GameType,
    dice_value: int,
    points: int,
    meta: Optional[dict[str, Any]] = None,
) -> None:
    await conn.execute(
        """
        INSERT INTO game_plays (telegram_id, user_label, game_type, dice_value, points, meta_json, played_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(telegram_id),
            str(user_label or "").strip() or "Игрок",
            game_type,
            int(dice_value),
            int(points),
            json.dumps(meta or {}, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    await conn.commit()


async def _weekly_rank_map(
    conn: aiosqlite.Connection,
    week_start: datetime,
    week_end: datetime,
) -> dict[int, tuple[int, int]]:
    cur = await conn.execute(
        """
        SELECT telegram_id, SUM(points) AS pts, COUNT(*) AS games
        FROM game_plays
        WHERE datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
          AND game_type IN ('blackjack', 'mines')
        GROUP BY telegram_id
        ORDER BY pts DESC, games DESC, telegram_id ASC
        """,
        (week_start.isoformat(), week_end.isoformat()),
    )
    rows = await cur.fetchall()
    out: dict[int, tuple[int, int]] = {}
    for rank, row in enumerate(rows, start=1):
        out[int(row["telegram_id"])] = (rank, int(row["pts"]))
    return out


async def get_user_rank(
    conn: aiosqlite.Connection,
    telegram_id: int,
    *,
    when: Optional[datetime] = None,
) -> Optional[int]:
    week_start, week_end, _ = week_bounds(when)
    ranks = await _weekly_rank_map(conn, week_start, week_end)
    item = ranks.get(int(telegram_id))
    return item[0] if item else None


async def weekly_summary(
    conn: aiosqlite.Connection,
    *,
    when: Optional[datetime] = None,
    viewer_id: Optional[int] = None,
    top_limit: int = 10,
) -> dict[str, Any]:
    week_start, week_end, period_label = week_bounds(when)
    week_params = (week_start.isoformat(), week_end.isoformat())

    cur = await conn.execute(
        f"""
        SELECT
            g.telegram_id,
            SUM(g.points) AS points,
            COUNT(*) AS games
        FROM game_plays g
        WHERE datetime(g.played_at) >= datetime(?) AND datetime(g.played_at) < datetime(?)
          AND g.game_type IN ('blackjack', 'mines')
        GROUP BY g.telegram_id
        ORDER BY points DESC, games DESC, g.telegram_id ASC
        LIMIT {int(top_limit)}
        """,
        week_params,
    )
    top = []
    for r in await cur.fetchall():
        tid = int(r["telegram_id"])
        top.append(
            {
                "telegram_id": tid,
                "label": await resolve_player_label(conn, tid),
                "points": int(r["points"]),
                "games": int(r["games"]),
            }
        )

    stats_row = await (
        await conn.execute(
            """
            SELECT
                COUNT(*) AS total_games,
                COUNT(DISTINCT telegram_id) AS unique_players,
                SUM(CASE WHEN game_type = 'blackjack' AND points = 70 THEN 1 ELSE 0 END) AS naturals,
                SUM(CASE WHEN game_type = 'blackjack' AND points IN (40, 70) THEN 1 ELSE 0 END) AS bj_wins,
                SUM(CASE WHEN game_type = 'mines' AND points >= 10 THEN 1 ELSE 0 END) AS mines_wins
            FROM game_plays
            WHERE datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
              AND game_type IN ('blackjack', 'mines')
            """,
            week_params,
        )
    ).fetchone()

    summary = {
        "total_games": int(stats_row["total_games"] or 0) if stats_row else 0,
        "unique_players": int(stats_row["unique_players"] or 0) if stats_row else 0,
        "naturals": int(stats_row["naturals"] or 0) if stats_row else 0,
        "bj_wins": int(stats_row["bj_wins"] or 0) if stats_row else 0,
        "mines_wins": int(stats_row["mines_wins"] or 0) if stats_row else 0,
    }

    viewer: Optional[dict[str, Any]] = None
    if viewer_id is not None:
        viewer = await user_week_stats(conn, int(viewer_id), when=when)

    return {
        "period_label": period_label,
        "top": top,
        "summary": summary,
        "viewer": viewer,
    }


async def user_week_stats(
    conn: aiosqlite.Connection,
    telegram_id: int,
    *,
    when: Optional[datetime] = None,
) -> dict[str, Any]:
    week_start, week_end, period_label = week_bounds(when)
    params = (int(telegram_id), week_start.isoformat(), week_end.isoformat())

    row = await (
        await conn.execute(
            """
            SELECT
                SUM(points) AS total_points,
                COUNT(*) AS games,
                SUM(CASE WHEN game_type = 'blackjack' THEN 1 ELSE 0 END) AS bj_games,
                SUM(CASE WHEN game_type = 'mines' THEN 1 ELSE 0 END) AS mines_games,
                SUM(CASE WHEN game_type = 'blackjack' THEN points ELSE 0 END) AS bj_points,
                SUM(CASE WHEN game_type = 'mines' THEN points ELSE 0 END) AS mines_points,
                SUM(CASE WHEN game_type = 'blackjack' AND points = 70 THEN 1 ELSE 0 END) AS naturals
            FROM game_plays
            WHERE telegram_id = ? AND datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
              AND game_type IN ('blackjack', 'mines')
            """,
            params,
        )
    ).fetchone()

    label = await resolve_player_label(conn, int(telegram_id))
    week = {
        "total_points": int(row["total_points"] or 0) if row else 0,
        "games": int(row["games"] or 0) if row else 0,
        "bj_games": int(row["bj_games"] or 0) if row else 0,
        "mines_games": int(row["mines_games"] or 0) if row else 0,
        "bj_points": int(row["bj_points"] or 0) if row else 0,
        "mines_points": int(row["mines_points"] or 0) if row else 0,
        "naturals": int(row["naturals"] or 0) if row else 0,
        "bj_wins": 0,
        "mines_cashouts": 0,
        "rank": 0,
    }

    bj_cur = await conn.execute(
        """
        SELECT meta_json FROM game_plays
        WHERE telegram_id = ? AND game_type = 'blackjack'
          AND datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
        """,
        params,
    )
    for brow in await bj_cur.fetchall():
        try:
            bmeta = json.loads(str(brow["meta_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        outcome = str(bmeta.get("outcome") or "")
        if outcome in ("win", "blackjack"):
            week["bj_wins"] += 1

    mines_cur = await conn.execute(
        """
        SELECT meta_json FROM game_plays
        WHERE telegram_id = ? AND game_type = 'mines'
          AND datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
        """,
        params,
    )
    for mrow in await mines_cur.fetchall():
        try:
            mmeta = json.loads(str(mrow["meta_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        outcome = str(mmeta.get("outcome") or "")
        if outcome in ("cashout", "perfect"):
            week["mines_cashouts"] += 1

    rank = await get_user_rank(conn, telegram_id, when=when)
    week["rank"] = rank or 0

    best_bj = await (
        await conn.execute(
            """
            SELECT points FROM game_plays
            WHERE telegram_id = ? AND game_type = 'blackjack'
            ORDER BY points DESC, played_at DESC
            LIMIT 1
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    best_mines = await (
        await conn.execute(
            """
            SELECT points, meta_json FROM game_plays
            WHERE telegram_id = ? AND game_type = 'mines'
            ORDER BY points DESC, played_at DESC
            LIMIT 1
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    best: list[str] = []
    if best_bj:
        pts = int(best_bj["points"])
        if pts >= 70:
            best.append(f"🃏 BLACKJACK (+{pts} очков)")
        else:
            best.append(f"🃏 блэкджек (+{pts} очков)")
    if best_mines:
        pts = int(best_mines["points"])
        try:
            mmeta = json.loads(str(best_mines["meta_json"] or "{}"))
            mult = float(mmeta.get("multiplier") or 0)
            best.append(f"💣 мины ×{mult:.2f} (+{pts} очков)")
        except (json.JSONDecodeError, TypeError, ValueError):
            best.append(f"💣 мины (+{pts} очков)")

    return {
        "label": label,
        "period_label": period_label,
        "week": week,
        "best": best,
        "points": week["total_points"],
        "rank": week["rank"],
    }


async def format_morning_games_digest(
    conn: aiosqlite.Connection,
    *,
    when: Optional[datetime] = None,
) -> str:
    """Утренний пост: топ мини-игр за неделю."""
    tz = get_timezone("Europe/Moscow")
    ref = (when or datetime.now(timezone.utc)).astimezone(tz)
    _, _, current_label = week_bounds(ref)

    if ref.weekday() == 0:
        prev_when = ref - timedelta(days=1)
        data = await weekly_summary(conn, when=prev_when, top_limit=5)
        title = f"☀️ Доброе утро, покерная братва!\n\n🎮 Мини-игры — итоги прошлой недели ({data['period_label']})"
    else:
        data = await weekly_summary(conn, when=ref, top_limit=5)
        title = f"☀️ Доброе утро, покерная братва!\n\n🎮 Мини-игры — топ недели ({current_label})"

    lines = [title, ""]
    top = data.get("top") or []
    if not top:
        lines.extend(
            [
                "Пока никто не играл — самое время начать!",
                "",
                "🃏 `/blackjack` · 💣 `/mines` · 📖 `/games help`",
            ]
        )
    else:
        medals = ("🥇", "🥈", "🥉", "4.", "5.")
        for i, row in enumerate(top[:5]):
            medal = medals[i] if i < len(medals) else f"{i + 1}."
            lines.append(f"{medal} {row['label']} — {row['points']} очков ({row['games']} игр)")
        summary = data.get("summary") or {}
        lines.extend(
            [
                "",
                f"📊 Партий: {summary.get('total_games', 0)} · "
                f"Игроков: {summary.get('unique_players', 0)} · "
                f"🃏 натуральных 21: {summary.get('naturals', 0)} · "
                f"побед BJ: {summary.get('bj_wins', 0)} · "
                f"кэшаутов мин: {summary.get('mines_wins', 0)}",
            ]
        )

    lines.extend(
        [
            "",
            "🍀 Удачного дня за столами!",
            "",
            "Полный топ: `/games` · Справка: `/games help`",
        ]
    )
    return "\n".join(lines)
