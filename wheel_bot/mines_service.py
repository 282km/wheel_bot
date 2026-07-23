from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Literal, Optional

import aiosqlite

from wheel_bot.db import utc_now_iso
from wheel_bot.game_service import get_user_rank, record_play

Outcome = Literal["cashout", "bust", "perfect"]

GRID_SIZE = 5
CELL_COUNT = GRID_SIZE * GRID_SIZE
DEFAULT_MINES = 3
ALLOWED_MINES = (3, 5, 10)
HOUSE_EDGE = 0.97
MAX_POINTS = 150

MINES_SCHEMA = """
CREATE TABLE IF NOT EXISTS mines_sessions (
    telegram_id INTEGER PRIMARY KEY,
    user_label TEXT NOT NULL DEFAULT '',
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    mine_count INTEGER NOT NULL,
    mines_json TEXT NOT NULL,
    opened_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mines_ui (
    telegram_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    board_message_id INTEGER,
    command_message_id INTEGER
);
"""


async def _migrate_mines_sessions(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(mines_sessions)")
    cols = {str(row[1]) for row in await cur.fetchall()}
    if cols and "message_id" not in cols:
        await conn.execute("ALTER TABLE mines_sessions ADD COLUMN message_id INTEGER")


async def ensure_mines_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(MINES_SCHEMA)
    await _migrate_mines_sessions(conn)
    await conn.commit()


def _safe_cells(mine_count: int) -> int:
    return CELL_COUNT - int(mine_count)


def multiplier_for(mine_count: int, opened_safe: int) -> float:
    if opened_safe <= 0:
        return 1.0
    total = CELL_COUNT
    safe = _safe_cells(mine_count)
    opens = min(int(opened_safe), safe)
    mult = 1.0
    for i in range(opens):
        mult *= (total - i) / (safe - i)
    return mult * HOUSE_EDGE


def points_for_outcome(outcome: Outcome, *, multiplier: float) -> int:
    if outcome == "bust":
        return 5
    raw = int(15 * multiplier)
    return min(MAX_POINTS, max(10, raw))


def _generate_mines(mine_count: int) -> list[int]:
    count = int(mine_count)
    if count not in ALLOWED_MINES:
        count = DEFAULT_MINES
    rng = secrets.SystemRandom()
    return sorted(rng.sample(range(CELL_COUNT), count))


@dataclass
class MinesUiState:
    telegram_id: int
    chat_id: int
    board_message_id: Optional[int] = None
    command_message_id: Optional[int] = None


@dataclass
class MinesSession:
    telegram_id: int
    user_label: str
    chat_id: int
    mine_count: int
    mines: list[int]
    opened: list[int]
    message_id: Optional[int] = None

    @property
    def mine_set(self) -> set[int]:
        return set(self.mines)

    @property
    def opened_set(self) -> set[int]:
        return set(self.opened)


@dataclass
class MinesView:
    text: str
    finished: bool
    opened: frozenset[int]
    mine_count: int
    multiplier: float
    can_cashout: bool
    reveal_mines: bool
    mines: frozenset[int]
    outcome: Optional[Outcome] = None
    points: int = 0
    flair: str = ""


async def get_ui_state(conn: aiosqlite.Connection, telegram_id: int) -> Optional[MinesUiState]:
    row = await (
        await conn.execute(
            """
            SELECT telegram_id, chat_id, board_message_id, command_message_id
            FROM mines_ui WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    return MinesUiState(
        telegram_id=int(row["telegram_id"]),
        chat_id=int(row["chat_id"]),
        board_message_id=int(row["board_message_id"]) if row["board_message_id"] is not None else None,
        command_message_id=int(row["command_message_id"]) if row["command_message_id"] is not None else None,
    )


async def save_ui_state(conn: aiosqlite.Connection, state: MinesUiState) -> None:
    await conn.execute(
        """
        INSERT INTO mines_ui (telegram_id, chat_id, board_message_id, command_message_id)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            chat_id = excluded.chat_id,
            board_message_id = excluded.board_message_id,
            command_message_id = excluded.command_message_id
        """,
        (
            int(state.telegram_id),
            int(state.chat_id),
            state.board_message_id,
            state.command_message_id,
        ),
    )
    await conn.commit()


async def get_session(conn: aiosqlite.Connection, telegram_id: int) -> Optional[MinesSession]:
    row = await (
        await conn.execute(
            """
            SELECT telegram_id, user_label, chat_id, message_id, mine_count, mines_json, opened_json
            FROM mines_sessions WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    return MinesSession(
        telegram_id=int(row["telegram_id"]),
        user_label=str(row["user_label"]),
        chat_id=int(row["chat_id"]),
        mine_count=int(row["mine_count"]),
        mines=json.loads(str(row["mines_json"])),
        opened=json.loads(str(row["opened_json"] or "[]")),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
    )


async def save_session(conn: aiosqlite.Connection, session: MinesSession) -> None:
    await conn.execute(
        """
        INSERT INTO mines_sessions
            (telegram_id, user_label, chat_id, message_id, mine_count, mines_json, opened_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            user_label = excluded.user_label,
            chat_id = excluded.chat_id,
            message_id = excluded.message_id,
            mine_count = excluded.mine_count,
            mines_json = excluded.mines_json,
            opened_json = excluded.opened_json,
            updated_at = excluded.updated_at
        """,
        (
            session.telegram_id,
            session.user_label,
            session.chat_id,
            session.message_id,
            session.mine_count,
            json.dumps(session.mines, ensure_ascii=False),
            json.dumps(session.opened, ensure_ascii=False),
            utc_now_iso(),
        ),
    )
    await conn.commit()


async def set_board_message_id(
    conn: aiosqlite.Connection,
    telegram_id: int,
    message_id: int,
) -> None:
    await conn.execute(
        """
        UPDATE mines_sessions
        SET message_id = ?, updated_at = ?
        WHERE telegram_id = ?
        """,
        (int(message_id), utc_now_iso(), int(telegram_id)),
    )
    await conn.commit()


async def clear_session(conn: aiosqlite.Connection, telegram_id: int) -> None:
    await conn.execute("DELETE FROM mines_sessions WHERE telegram_id = ?", (int(telegram_id),))
    await conn.commit()


def _cell_emoji(idx: int, session: MinesSession, *, reveal_mines: bool) -> str:
    if idx in session.opened_set:
        return "💣" if idx in session.mine_set else "💎"
    if reveal_mines and idx in session.mine_set:
        return "💣"
    return "⬜"


def _format_grid(session: MinesSession, *, reveal_mines: bool) -> str:
    rows: list[str] = []
    for r in range(GRID_SIZE):
        cells = [_cell_emoji(r * GRID_SIZE + c, session, reveal_mines=reveal_mines) for c in range(GRID_SIZE)]
        rows.append(" ".join(cells))
    return "\n".join(rows)


def _opened_safe_count(session: MinesSession) -> int:
    return sum(1 for idx in session.opened if idx not in session.mine_set)


def _format_active(session: MinesSession, user_label: str) -> str:
    opened_safe = _opened_safe_count(session)
    mult = multiplier_for(session.mine_count, opened_safe)
    lines = [
        f"💣 *Мины* — {user_label}",
        "",
        f"{session.mine_count} мины · открыто {opened_safe} · ×{mult:.2f}",
        "",
        _format_grid(session, reveal_mines=False),
        "",
        "Открывайте ⬜ — или заберите выигрыш кнопкой 💰",
        f"👆 Кнопки ниже — только для {user_label}",
    ]
    return "\n".join(lines)


def _format_result(
    session: MinesSession,
    user_label: str,
    outcome: Outcome,
    flair: str,
    points: int,
    *,
    multiplier: float,
) -> str:
    opened_safe = _opened_safe_count(session)
    lines = [
        f"💣 *Мины* — {user_label}",
        "",
        f"{session.mine_count} мины · открыто {opened_safe} · ×{multiplier:.2f}",
        "",
        _format_grid(session, reveal_mines=True),
        "",
        flair,
        f"💎 +{points} очков",
    ]
    return "\n".join(lines)


def _view_from_session(
    session: MinesSession,
    user_label: str,
    *,
    finished: bool,
    outcome: Optional[Outcome] = None,
    flair: str = "",
    points: int = 0,
) -> MinesView:
    opened_safe = _opened_safe_count(session)
    mult = multiplier_for(session.mine_count, opened_safe)
    if finished:
        text = _format_result(
            session,
            user_label,
            outcome or "cashout",
            flair,
            points,
            multiplier=mult,
        )
    else:
        text = _format_active(session, user_label)
    return MinesView(
        text=text,
        finished=finished,
        opened=frozenset(session.opened),
        mine_count=session.mine_count,
        multiplier=mult,
        can_cashout=not finished and opened_safe > 0,
        reveal_mines=finished,
        mines=frozenset(session.mines),
        outcome=outcome,
        points=points,
        flair=flair,
    )


async def _finish_session(
    conn: aiosqlite.Connection,
    session: MinesSession,
    user_label: str,
    outcome: Outcome,
    *,
    flair: str,
) -> MinesView:
    opened_safe = _opened_safe_count(session)
    mult = multiplier_for(session.mine_count, opened_safe)
    points = points_for_outcome(outcome, multiplier=mult)

    await record_play(
        conn,
        telegram_id=session.telegram_id,
        user_label=user_label,
        game_type="mines",
        dice_value=opened_safe,
        points=points,
        meta={
            "outcome": outcome,
            "mine_count": session.mine_count,
            "opened_safe": opened_safe,
            "multiplier": round(mult, 2),
            "mines": session.mines,
        },
    )
    await clear_session(conn, session.telegram_id)
    rank = await get_user_rank(conn, session.telegram_id)

    view = _view_from_session(
        session,
        user_label,
        finished=True,
        outcome=outcome,
        flair=flair,
        points=points,
    )
    text = view.text
    if rank is not None:
        text += f"\n🏆 Место в недельном топе: {rank}-е"
    text += "\n\n📊 Топ: `/games`"
    return MinesView(
        text=text,
        finished=True,
        opened=view.opened,
        mine_count=view.mine_count,
        multiplier=view.multiplier,
        can_cashout=False,
        reveal_mines=True,
        mines=view.mines,
        outcome=outcome,
        points=points,
        flair=flair,
    )


def _normalize_mine_count(mine_count: int) -> int:
    count = int(mine_count)
    return count if count in ALLOWED_MINES else DEFAULT_MINES


async def start_mines(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    chat_id: int,
    mine_count: int = DEFAULT_MINES,
) -> tuple[Optional[str], Optional[MinesView]]:
    existing = await get_session(conn, telegram_id)
    if existing is not None:
        await clear_session(conn, telegram_id)

    count = _normalize_mine_count(mine_count)
    session = MinesSession(
        telegram_id=int(telegram_id),
        user_label=user_label,
        chat_id=int(chat_id),
        mine_count=count,
        mines=_generate_mines(count),
        opened=[],
    )
    await save_session(conn, session)
    return None, _view_from_session(session, user_label, finished=False)


async def open_mines_cell(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    cell: int,
) -> tuple[Optional[str], Optional[MinesView]]:
    session = await get_session(conn, telegram_id)
    if session is None:
        return "Нет активной игры. Начните: `/mines`", None

    idx = int(cell)
    if idx < 0 or idx >= CELL_COUNT:
        return "Неверная клетка.", None
    if idx in session.opened_set:
        return "Эта клетка уже открыта.", None

    session.opened.append(idx)
    if idx in session.mine_set:
        view = await _finish_session(
            conn,
            session,
            user_label,
            "bust",
            flair="💥 Бум! Попали на мину.",
        )
        return None, view

    safe_total = _safe_cells(session.mine_count)
    opened_safe = _opened_safe_count(session)
    if opened_safe >= safe_total:
        view = await _finish_session(
            conn,
            session,
            user_label,
            "perfect",
            flair="🏆 Идеально! Все безопасные клетки открыты!",
        )
        return None, view

    await save_session(conn, session)
    return None, _view_from_session(session, user_label, finished=False)


async def cashout_mines(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
) -> tuple[Optional[str], Optional[MinesView]]:
    session = await get_session(conn, telegram_id)
    if session is None:
        return "Нет активной игры. Начните: `/mines`", None
    if _opened_safe_count(session) <= 0:
        return "Сначала откройте хотя бы одну безопасную клетку.", None

    opened_safe = _opened_safe_count(session)
    mult = multiplier_for(session.mine_count, opened_safe)
    view = await _finish_session(
        conn,
        session,
        user_label,
        "cashout",
        flair=f"💰 Забрали ×{mult:.2f} — аккуратная игра!",
    )
    return None, view
