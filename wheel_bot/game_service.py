from __future__ import annotations

import json
import secrets
from datetime import datetime, time, timedelta, timezone
from typing import Any, Literal, Optional

import aiosqlite

from wheel_bot.db import utc_now_iso
from wheel_bot.timezones import get_timezone

GameType = Literal["slots", "bowling", "duel"]

SLOTS_COOLDOWN = timedelta(minutes=10)
BOWLING_COOLDOWN = timedelta(minutes=10)
DUEL_COOLDOWN = timedelta(hours=1)
DUEL_MAX_PER_HOUR = 3

SLOTS_JACKPOT_VALUES = frozenset({1, 22, 43, 64})

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
    await conn.commit()


def _parse_iso(ts: str) -> datetime:
    return datetime.fromisoformat(ts).astimezone(timezone.utc)


def format_wait(seconds: int) -> str:
    seconds = max(0, int(seconds))
    minutes, sec = divmod(seconds, 60)
    if minutes and sec:
        return f"{minutes} мин {sec} сек"
    if minutes:
        return f"{minutes} мин"
    if sec:
        return f"{sec} сек"
    return "меньше минуты"


def week_bounds(when: Optional[datetime] = None, *, tz_name: str = "Europe/Moscow") -> tuple[datetime, datetime, str]:
    tz = get_timezone(tz_name)
    ref = (when or datetime.now(timezone.utc)).astimezone(tz)
    monday = ref.date() - timedelta(days=ref.weekday())
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = start + timedelta(days=7)
    sunday = monday + timedelta(days=6)
    label = f"{monday.strftime('%d.%m')}–{sunday.strftime('%d.%m.%Y')}"
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc), label


def score_slots(value: int) -> tuple[int, str]:
    v = int(value)
    if v in SLOTS_JACKPOT_VALUES:
        return 100, secrets.choice(
            (
                "JACKPOT! Три в ряд — сегодня можно идти в олл-ин… шучу 🎰",
                "JACKPOT! Барабаны сошлись идеально!",
                "JACKPOT! Такой спин достаётся редко!",
            )
        )
    if v >= 50:
        return 40, "Хороший спин — фортуна на вашей стороне!"
    if v >= 20:
        return 25, "Неплохо — ещё чуть-чуть до джекпота."
    return 10, secrets.choice(
        (
            "Обычный спин — банк ушёл мимо.",
            "Ривер не доехал, но очки в копилку.",
            "Фортуна моргнула — попробуйте позже.",
        )
    )


def score_bowling(value: int) -> tuple[int, str]:
    v = int(value)
    if v >= 6:
        return 60, "СТРАЙК! Шар унёс все кегли! 🔥"
    if v == 5:
        return 45, "Отличный кадр — почти страйк!"
    if v == 4:
        return 30, "Хороший бросок!"
    if v == 3:
        return 15, "Средненько — дорожка живая."
    return 5, "Соскользнул с дорожки — бывает!"


async def _last_play_at(
    conn: aiosqlite.Connection,
    telegram_id: int,
    game_type: GameType,
) -> Optional[datetime]:
    row = await (
        await conn.execute(
            """
            SELECT played_at FROM game_plays
            WHERE telegram_id = ? AND game_type = ?
            ORDER BY played_at DESC
            LIMIT 1
            """,
            (int(telegram_id), game_type),
        )
    ).fetchone()
    if not row:
        return None
    return _parse_iso(str(row["played_at"]))


async def check_cooldown(
    conn: aiosqlite.Connection,
    telegram_id: int,
    game_type: GameType,
    *,
    now: Optional[datetime] = None,
) -> Optional[int]:
    ref = now or datetime.now(timezone.utc)
    if game_type == "duel":
        since = (ref - DUEL_COOLDOWN).isoformat()
        row = await (
            await conn.execute(
                """
                SELECT COUNT(*) AS c FROM game_plays
                WHERE telegram_id = ? AND game_type = 'duel'
                  AND played_at >= ?
                """,
                (int(telegram_id), since),
            )
        ).fetchone()
        count = int(row["c"]) if row else 0
        if count >= DUEL_MAX_PER_HOUR:
            last = await _last_play_at(conn, telegram_id, "duel")
            if last is None:
                return int(DUEL_COOLDOWN.total_seconds())
            wait = int((last + DUEL_COOLDOWN - ref).total_seconds())
            return max(1, wait)
        return None

    delta = SLOTS_COOLDOWN if game_type == "slots" else BOWLING_COOLDOWN
    last = await _last_play_at(conn, telegram_id, game_type)
    if last is None:
        return None
    elapsed = ref - last
    if elapsed >= delta:
        return None
    return max(1, int((delta - elapsed).total_seconds()))


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
    params = (week_start.isoformat(), week_end.isoformat())

    cur = await conn.execute(
        f"""
        SELECT
            telegram_id,
            COALESCE(NULLIF(MAX(user_label), ''), 'Игрок') AS label,
            SUM(points) AS points,
            COUNT(*) AS games
        FROM game_plays
        WHERE datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
        GROUP BY telegram_id
        ORDER BY points DESC, games DESC, label COLLATE NOCASE ASC
        LIMIT {int(top_limit)}
        """,
        params,
    )
    top = [
        {
            "telegram_id": int(r["telegram_id"]),
            "label": str(r["label"]),
            "points": int(r["points"]),
            "games": int(r["games"]),
        }
        for r in await cur.fetchall()
    ]

    stats_row = await (
        await conn.execute(
            """
            SELECT
                COUNT(*) AS total_games,
                COUNT(DISTINCT telegram_id) AS unique_players,
                SUM(CASE WHEN game_type = 'duel' THEN 1 ELSE 0 END) AS duels,
                SUM(CASE WHEN game_type = 'slots' AND dice_value IN (1,22,43,64) THEN 1 ELSE 0 END) AS jackpots,
                SUM(CASE WHEN game_type = 'bowling' AND dice_value = 6 THEN 1 ELSE 0 END) AS strikes
            FROM game_plays
            WHERE datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
            """,
            params,
        )
    ).fetchone()

    summary = {
        "total_games": int(stats_row["total_games"]) if stats_row else 0,
        "unique_players": int(stats_row["unique_players"]) if stats_row else 0,
        "duels": int(stats_row["duels"]) if stats_row else 0,
        "jackpots": int(stats_row["jackpots"]) if stats_row else 0,
        "strikes": int(stats_row["strikes"]) if stats_row else 0,
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
                COALESCE(NULLIF(MAX(user_label), ''), 'Игрок') AS label,
                SUM(points) AS total_points,
                SUM(CASE WHEN game_type = 'slots' THEN 1 ELSE 0 END) AS slots_games,
                SUM(CASE WHEN game_type = 'slots' THEN points ELSE 0 END) AS slots_points,
                SUM(CASE WHEN game_type = 'bowling' THEN 1 ELSE 0 END) AS bowling_games,
                SUM(CASE WHEN game_type = 'bowling' THEN points ELSE 0 END) AS bowling_points,
                SUM(CASE WHEN game_type = 'slots' AND dice_value IN (1,22,43,64) THEN 1 ELSE 0 END) AS jackpots,
                SUM(CASE WHEN game_type = 'bowling' AND dice_value = 6 THEN 1 ELSE 0 END) AS strikes
            FROM game_plays
            WHERE telegram_id = ? AND datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
            """,
            params,
        )
    ).fetchone()

    label = str(row["label"]) if row and row["label"] else "Игрок"
    week = {
        "total_points": int(row["total_points"] or 0) if row else 0,
        "slots_games": int(row["slots_games"] or 0) if row else 0,
        "slots_points": int(row["slots_points"] or 0) if row else 0,
        "bowling_games": int(row["bowling_games"] or 0) if row else 0,
        "bowling_points": int(row["bowling_points"] or 0) if row else 0,
        "jackpots": int(row["jackpots"] or 0) if row else 0,
        "strikes": int(row["strikes"] or 0) if row else 0,
        "duel_wins": 0,
        "duel_losses": 0,
        "duel_ties": 0,
        "rank": 0,
    }

    duel_cur = await conn.execute(
        """
        SELECT meta_json FROM game_plays
        WHERE telegram_id = ? AND game_type = 'duel'
          AND datetime(played_at) >= datetime(?) AND datetime(played_at) < datetime(?)
        """,
        params,
    )
    for drow in await duel_cur.fetchall():
        try:
            meta = json.loads(str(drow["meta_json"] or "{}"))
        except json.JSONDecodeError:
            continue
        outcome = str(meta.get("outcome") or "")
        if outcome == "win":
            week["duel_wins"] += 1
        elif outcome == "loss":
            week["duel_losses"] += 1
        elif outcome == "tie":
            week["duel_ties"] += 1

    rank = await get_user_rank(conn, telegram_id, when=when)
    week["rank"] = rank or 0

    best_row = await (
        await conn.execute(
            """
            SELECT game_type, dice_value, points FROM game_plays
            WHERE telegram_id = ?
            ORDER BY points DESC, played_at DESC
            LIMIT 1
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    best: Optional[str] = None
    if best_row:
        gt = str(best_row["game_type"])
        dv = int(best_row["dice_value"])
        pts = int(best_row["points"])
        if gt == "slots" and dv in SLOTS_JACKPOT_VALUES:
            best = f"🎰 JACKPOT (+{pts} очков)"
        elif gt == "bowling" and dv == 6:
            best = f"🎳 страйк (+{pts} очков)"
        else:
            best = f"+{pts} очков ({gt})"

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
    """Утренний пост: приветствие + итоги прошлой недели + текущий топ."""
    tz = get_timezone("Europe/Moscow")
    ref = (when or datetime.now(timezone.utc)).astimezone(tz)
    _, _, current_label = week_bounds(ref)

    # Утром в понедельник — итоги прошлой недели; иначе — текущая неделя.
    if ref.weekday() == 0:
        prev_when = ref - timedelta(days=1)
        data = await weekly_summary(conn, when=prev_when, top_limit=5)
        title = f"☀️ Доброе утро, покерная братва!\n\n🏆 Итоги прошлой недели ({data['period_label']})"
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
                "🎰 `/slots` · 🎳 `/bowling` · ⚔️ `/duel`",
                "📖 Инструкция: `/games help`",
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
                f"📊 Игр: {summary.get('total_games', 0)} · "
                f"Игроков: {summary.get('unique_players', 0)} · "
                f"🎰 джекпотов: {summary.get('jackpots', 0)} · "
                f"🎳 страйков: {summary.get('strikes', 0)}",
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
