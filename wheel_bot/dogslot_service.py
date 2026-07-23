from __future__ import annotations

import json
import secrets
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import aiosqlite

from wheel_bot.db import utc_now_iso
from wheel_bot.game_service import get_user_rank, record_play

Phase = Literal["bonus_pick", "free_spins"]

COLS = 5
ROWS = 3
SCATTER_COLS = (0, 2, 4)
WILD_COLS = (1, 2, 3)

SCATTER = "scatter"
WILD = "wild"
PAY_SYMBOLS = ("rottweiler", "pug", "shiba", "bone", "collar")

SYMBOL_EMOJI = {
    "scatter": "🐾",
    "wild": "🏠",
    "rottweiler": "🐕",
    "pug": "🐶",
    "shiba": "🦮",
    "bone": "🦴",
    "collar": "🎀",
}

PAYTABLE: dict[str, tuple[int, int, int]] = {
    "rottweiler": (15, 30, 60),
    "shiba": (15, 30, 60),
    "pug": (10, 20, 40),
    "bone": (5, 10, 20),
    "collar": (5, 10, 20),
}

PAYLINES: tuple[tuple[tuple[int, int], ...], ...] = (
    tuple((0, c) for c in range(COLS)),
    tuple((1, c) for c in range(COLS)),
    tuple((2, c) for c in range(COLS)),
)

MAX_BONUS_POINTS = 500

DOGSLOT_SCHEMA = """
CREATE TABLE IF NOT EXISTS dogslot_sessions (
    telegram_id INTEGER PRIMARY KEY,
    user_label TEXT NOT NULL DEFAULT '',
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    phase TEXT NOT NULL,
    free_spins_left INTEGER NOT NULL DEFAULT 0,
    free_spins_total INTEGER NOT NULL DEFAULT 0,
    bonus_points INTEGER NOT NULL DEFAULT 0,
    base_points INTEGER NOT NULL DEFAULT 0,
    sticky_json TEXT NOT NULL DEFAULT '{}',
    pick_json TEXT NOT NULL DEFAULT '[]',
    grid_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS dogslot_ui (
    telegram_id INTEGER PRIMARY KEY,
    chat_id INTEGER NOT NULL,
    board_message_id INTEGER,
    command_message_id INTEGER
);
"""

# Веса символов по барабанам (col 0..4)
_BASE_STRIPS: dict[int, tuple[str, ...]] = {
    0: ("rottweiler", "pug", "shiba", "bone", "collar", "scatter", "rottweiler", "pug", "bone"),
    1: ("rottweiler", "pug", "wild", "bone", "collar", "shiba", "wild", "pug", "bone"),
    2: ("shiba", "rottweiler", "wild", "pug", "collar", "scatter", "wild", "bone", "pug"),
    3: ("pug", "shiba", "wild", "rottweiler", "bone", "wild", "collar", "pug", "bone"),
    4: ("collar", "bone", "pug", "scatter", "shiba", "rottweiler", "scatter", "pug", "bone"),
}


@dataclass
class Cell:
    symbol: str
    mult: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {"symbol": self.symbol, "mult": int(self.mult)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Cell:
        return cls(str(raw.get("symbol") or "bone"), int(raw.get("mult") or 0))


@dataclass
class DogslotUiState:
    telegram_id: int
    chat_id: int
    board_message_id: Optional[int] = None
    command_message_id: Optional[int] = None


@dataclass
class DogslotSession:
    telegram_id: int
    user_label: str
    chat_id: int
    phase: Phase
    free_spins_left: int = 0
    free_spins_total: int = 0
    bonus_points: int = 0
    base_points: int = 0
    sticky_wilds: dict[str, int] = field(default_factory=dict)
    pick_values: list[int] = field(default_factory=list)
    grid: list[list[Cell]] = field(default_factory=list)
    message_id: Optional[int] = None


@dataclass
class DogslotView:
    text: str
    action: Literal["spin", "pick", "free_spin", "none"]
    can_act: bool
    wait_seconds: int = 0


async def ensure_dogslot_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(DOGSLOT_SCHEMA)
    await conn.commit()


async def get_ui_state(conn: aiosqlite.Connection, telegram_id: int) -> Optional[DogslotUiState]:
    row = await (
        await conn.execute(
            """
            SELECT telegram_id, chat_id, board_message_id, command_message_id
            FROM dogslot_ui WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    return DogslotUiState(
        telegram_id=int(row["telegram_id"]),
        chat_id=int(row["chat_id"]),
        board_message_id=int(row["board_message_id"]) if row["board_message_id"] is not None else None,
        command_message_id=int(row["command_message_id"]) if row["command_message_id"] is not None else None,
    )


async def save_ui_state(conn: aiosqlite.Connection, state: DogslotUiState) -> None:
    await conn.execute(
        """
        INSERT INTO dogslot_ui (telegram_id, chat_id, board_message_id, command_message_id)
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


def _grid_from_json(raw: str) -> list[list[Cell]]:
    try:
        data = json.loads(raw or "[]")
    except json.JSONDecodeError:
        return []
    out: list[list[Cell]] = []
    for row in data:
        out.append([Cell.from_dict(c) for c in row])
    return out


def _grid_to_json(grid: list[list[Cell]]) -> str:
    return json.dumps([[c.to_dict() for c in row] for row in grid], ensure_ascii=False)


async def get_session(conn: aiosqlite.Connection, telegram_id: int) -> Optional[DogslotSession]:
    row = await (
        await conn.execute(
            """
            SELECT telegram_id, user_label, chat_id, message_id, phase,
                   free_spins_left, free_spins_total, bonus_points, base_points,
                   sticky_json, pick_json, grid_json
            FROM dogslot_sessions WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    try:
        sticky = json.loads(str(row["sticky_json"] or "{}"))
    except json.JSONDecodeError:
        sticky = {}
    try:
        pick_values = json.loads(str(row["pick_json"] or "[]"))
    except json.JSONDecodeError:
        pick_values = []
    return DogslotSession(
        telegram_id=int(row["telegram_id"]),
        user_label=str(row["user_label"]),
        chat_id=int(row["chat_id"]),
        phase=str(row["phase"]),  # type: ignore[arg-type]
        free_spins_left=int(row["free_spins_left"]),
        free_spins_total=int(row["free_spins_total"]),
        bonus_points=int(row["bonus_points"]),
        base_points=int(row["base_points"]),
        sticky_wilds={str(k): int(v) for k, v in sticky.items()},
        pick_values=[int(x) for x in pick_values],
        grid=_grid_from_json(str(row["grid_json"] or "[]")),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
    )


async def save_session(conn: aiosqlite.Connection, session: DogslotSession) -> None:
    await conn.execute(
        """
        INSERT INTO dogslot_sessions
            (telegram_id, user_label, chat_id, message_id, phase,
             free_spins_left, free_spins_total, bonus_points, base_points,
             sticky_json, pick_json, grid_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            user_label = excluded.user_label,
            chat_id = excluded.chat_id,
            message_id = excluded.message_id,
            phase = excluded.phase,
            free_spins_left = excluded.free_spins_left,
            free_spins_total = excluded.free_spins_total,
            bonus_points = excluded.bonus_points,
            base_points = excluded.base_points,
            sticky_json = excluded.sticky_json,
            pick_json = excluded.pick_json,
            grid_json = excluded.grid_json,
            updated_at = excluded.updated_at
        """,
        (
            session.telegram_id,
            session.user_label,
            session.chat_id,
            session.message_id,
            session.phase,
            session.free_spins_left,
            session.free_spins_total,
            session.bonus_points,
            session.base_points,
            json.dumps(session.sticky_wilds, ensure_ascii=False),
            json.dumps(session.pick_values, ensure_ascii=False),
            _grid_to_json(session.grid),
            utc_now_iso(),
        ),
    )
    await conn.commit()


async def set_board_message_id(conn: aiosqlite.Connection, telegram_id: int, message_id: int) -> None:
    await conn.execute(
        "UPDATE dogslot_sessions SET message_id = ?, updated_at = ? WHERE telegram_id = ?",
        (int(message_id), utc_now_iso(), int(telegram_id)),
    )
    await conn.commit()


async def sync_board_message_id(
    conn: aiosqlite.Connection,
    telegram_id: int,
    message_id: int,
) -> None:
    """Keep session board id in sync with the visible Telegram message."""
    session = await get_session(conn, telegram_id)
    if session is None:
        return
    if session.message_id == int(message_id):
        return
    await set_board_message_id(conn, telegram_id, message_id)


async def clear_session(conn: aiosqlite.Connection, telegram_id: int) -> None:
    await conn.execute("DELETE FROM dogslot_sessions WHERE telegram_id = ?", (int(telegram_id),))
    await conn.commit()


def _rng() -> secrets.SystemRandom:
    return secrets.SystemRandom()


def _spin_column(col: int, rng: secrets.SystemRandom) -> list[Cell]:
    strip = _BASE_STRIPS[col]
    cells: list[Cell] = []
    for _ in range(ROWS):
        sym = rng.choice(strip)
        if sym == WILD:
            cells.append(Cell(WILD, rng.choice((2, 3))))
        elif sym == SCATTER:
            cells.append(Cell(SCATTER))
        else:
            cells.append(Cell(sym))
    return cells


def _generate_grid(rng: secrets.SystemRandom) -> list[list[Cell]]:
    cols = [_spin_column(c, rng) for c in range(COLS)]
    return [[cols[c][r] for c in range(COLS)] for r in range(ROWS)]


def _apply_sticky(grid: list[list[Cell]], sticky: dict[str, int]) -> list[list[Cell]]:
    out = [[Cell(c.symbol, c.mult) for c in row] for row in grid]
    for key, mult in sticky.items():
        try:
            col_s, row_s = key.split(",")
            col, row = int(col_s), int(row_s)
        except ValueError:
            continue
        if 0 <= row < ROWS and 0 <= col < COLS:
            out[row][col] = Cell(WILD, int(mult))
    return out


def _collect_new_sticky(grid: list[list[Cell]], sticky: dict[str, int]) -> dict[str, int]:
    updated = dict(sticky)
    for row in range(ROWS):
        for col in WILD_COLS:
            cell = grid[row][col]
            if cell.symbol == WILD:
                key = f"{col},{row}"
                if key not in updated:
                    updated[key] = cell.mult or 2
    return updated


def _bonus_triggered(grid: list[list[Cell]]) -> bool:
    for col in SCATTER_COLS:
        if not any(grid[row][col].symbol == SCATTER for row in range(ROWS)):
            return False
    return True


def _format_cell(cell: Cell, *, sticky: bool = False) -> str:
    if cell.symbol == WILD:
        mult = cell.mult or 2
        mark = "📌" if sticky else ""
        return f"{mark}🏠×{mult}"
    return SYMBOL_EMOJI.get(cell.symbol, "❓")


def _format_grid(
    grid: list[list[Cell]],
    sticky: Optional[dict[str, int]] = None,
) -> str:
    sticky = sticky or {}
    lines: list[str] = []
    for row in range(ROWS):
        cells: list[str] = []
        for col in range(COLS):
            key = f"{col},{row}"
            cells.append(_format_cell(grid[row][col], sticky=key in sticky))
        lines.append(" ".join(cells))
    return "\n".join(lines)


def _line_payout(cells: list[Cell]) -> tuple[int, int]:
    target: Optional[str] = None
    for cell in cells:
        if cell.symbol in PAY_SYMBOLS:
            target = cell.symbol
            break
    if target is None:
        if sum(1 for c in cells if c.symbol == WILD) >= 3:
            target = "pug"
        else:
            return 0, 0

    count = 0
    mult = 1
    for cell in cells:
        if cell.symbol == target or cell.symbol == WILD:
            count += 1
            if cell.symbol == WILD and cell.mult > 1:
                mult *= cell.mult
        else:
            break
    if count < 3:
        return 0, count
    pay_idx = min(count, 5) - 3
    return int(PAYTABLE[target][pay_idx] * mult), count


def _score_grid(grid: list[list[Cell]]) -> tuple[int, list[str]]:
    total = 0
    notes: list[str] = []
    labels = ("верх", "центр", "низ")
    for idx, line in enumerate(PAYLINES):
        cells = [grid[r][c] for r, c in line]
        pts, n = _line_payout(cells)
        if pts > 0:
            total += pts
            notes.append(f"Линия {labels[idx]}: {n} в ряд → +{pts}")
    return total, notes


def _roll_pick_values(rng: secrets.SystemRandom) -> list[int]:
    return [rng.randint(1, 3) for _ in range(9)]


def _format_pick_grid(values: list[int]) -> str:
    if len(values) != 9:
        return "—"
    rows: list[str] = []
    for r in range(3):
        chunk = values[r * 3 : (r + 1) * 3]
        rows.append(" │ ".join(str(v) for v in chunk))
    return "\n".join(rows)


def _build_view(
    session: Optional[DogslotSession],
    user_label: str,
    *,
    lines: list[str],
    action: Literal["spin", "pick", "free_spin", "none"],
    can_act: bool,
    wait_seconds: int = 0,
    rank: Optional[int] = None,
) -> DogslotView:
    header = f"🐶 *The Dog House* — {user_label}"
    body = [header, ""]
    body.extend(lines)
    if rank is not None:
        body.extend(["", f"🏆 Место в недельном топе: {rank}-е"])
    if can_act:
        body.extend(["", "👆 Кнопка ниже — только для вас"])
    body.extend(["", "📊 Топ: `/games`"])
    return DogslotView(
        text="\n".join(body),
        action=action,
        can_act=can_act,
        wait_seconds=wait_seconds,
    )


async def _finish_bonus(
    conn: aiosqlite.Connection,
    session: DogslotSession,
    user_label: str,
) -> DogslotView:
    bonus_pts = min(MAX_BONUS_POINTS, int(session.bonus_points))
    total_record = int(session.base_points) + bonus_pts
    await record_play(
        conn,
        telegram_id=session.telegram_id,
        user_label=user_label,
        game_type="dogslot",
        dice_value=session.free_spins_total,
        points=total_record,
        meta={
            "mode": "bonus",
            "base_points": session.base_points,
            "bonus_points": bonus_pts,
            "free_spins": session.free_spins_total,
            "sticky_wilds": len(session.sticky_wilds),
        },
    )
    rank = await get_user_rank(conn, session.telegram_id)
    await clear_session(conn, session.telegram_id)
    lines = [
        "🎉 *Бонус завершён!*",
        "",
        f"🎰 Фри-спинов: {session.free_spins_total}",
        f"📌 Sticky wilds: {len(session.sticky_wilds)}",
        f"💎 Бонус: +{bonus_pts} очков",
    ]
    if session.base_points:
        lines.append(f"🎯 Базовый спин: +{session.base_points}")
    lines.append(f"✨ Всего за раунд: +{total_record} очков")
    return _build_view(
        None,
        user_label,
        lines=lines,
        action="spin",
        can_act=True,
        rank=rank,
    )


async def _start_bonus_pick(
    conn: aiosqlite.Connection,
    session: DogslotSession,
    user_label: str,
    grid: list[list[Cell]],
    base_points: int,
) -> DogslotView:
    session.phase = "bonus_pick"
    session.base_points = base_points
    session.bonus_points = 0
    session.sticky_wilds = {}
    session.pick_values = []
    session.grid = grid
    await save_session(conn, session)
    lines = [
        _format_grid(grid),
        "",
    ]
    if base_points > 0:
        lines.append(f"Выигрыш до бонуса: +{base_points} очков")
        lines.append("")
    lines.extend(
        [
            "🐾🐾🐾 *БОНУС!* Три лапы на барабанах 1–3–5!",
            "",
            "Сейчас раскрутим сетку 3×3 — в каждой ячейке 1, 2 или 3 фри-спина.",
            "Сумма = ваш бонус (от 9 до 27), как в оригинале.",
            "",
            "Во фри-спинах 🏠 на барабанах 2–3–4 липнут и несут ×2 или ×3.",
        ]
    )
    return _build_view(
        session,
        user_label,
        lines=lines,
        action="pick",
        can_act=True,
    )


async def run_bonus_pick(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
) -> tuple[Optional[str], Optional[DogslotView]]:
    session = await get_session(conn, telegram_id)
    if session is None or session.phase != "bonus_pick":
        return "Нет активного бонуса. Начните `/dogslot`.", None

    rng = _rng()
    values = _roll_pick_values(rng)
    total_spins = sum(values)
    session.pick_values = values
    session.phase = "free_spins"
    session.free_spins_left = total_spins
    session.free_spins_total = total_spins
    session.sticky_wilds = {}
    await save_session(conn, session)

    lines = [
        "🎁 *Выбор фри-спинов*",
        "",
        _format_pick_grid(values),
        "",
        f"➕ Сумма: *{total_spins} фри-спинов*",
        "",
        "🏠 Sticky wilds с множителями ×2/×3 — на средних барабанах.",
        f"Осталось спинов: {session.free_spins_left}",
    ]
    return None, _build_view(
        session,
        user_label,
        lines=lines,
        action="free_spin",
        can_act=True,
    )


async def run_free_spin(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
) -> tuple[Optional[str], Optional[DogslotView]]:
    session = await get_session(conn, telegram_id)
    if session is None or session.phase != "free_spins":
        return "Нет активных фри-спинов. Начните `/dogslot`.", None
    if session.free_spins_left <= 0:
        return None, await _finish_bonus(conn, session, user_label)

    rng = _rng()
    grid = _generate_grid(rng)
    session.sticky_wilds = _collect_new_sticky(grid, session.sticky_wilds)
    grid = _apply_sticky(grid, session.sticky_wilds)
    session.grid = grid

    spin_pts, notes = _score_grid(grid)
    session.bonus_points += spin_pts
    session.free_spins_left -= 1
    await save_session(conn, session)

    if session.free_spins_left <= 0:
        return None, await _finish_bonus(conn, session, user_label)

    lines = [
        f"🎰 *Фри-спин* · осталось {session.free_spins_left}/{session.free_spins_total}",
        "",
        _format_grid(grid, sticky=session.sticky_wilds),
        "",
    ]
    if notes:
        lines.extend(notes)
    else:
        lines.append("Без выигрыша на линиях.")
    lines.extend(
        [
            f"💎 За спин: +{spin_pts} · в бонусе: {session.bonus_points}",
            f"📌 Sticky wilds: {len(session.sticky_wilds)}",
        ]
    )
    return None, _build_view(
        session,
        user_label,
        lines=lines,
        action="free_spin",
        can_act=True,
    )


async def spin_dogslot(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    chat_id: int,
) -> tuple[Optional[str], Optional[DogslotView]]:
    session = await get_session(conn, telegram_id)
    if session is not None:
        if session.phase == "bonus_pick":
            return None, _build_view(
                session,
                user_label,
                lines=["Бонус уже ждёт — нажмите кнопку раскрутки 3×3."],
                action="pick",
                can_act=True,
            )
        if session.phase == "free_spins":
            return None, _build_view(
                session,
                user_label,
                lines=[f"Бонус идёт — осталось {session.free_spins_left} фри-спинов."],
                action="free_spin",
                can_act=True,
            )

    rng = _rng()
    grid = _generate_grid(rng)
    base_points, notes = _score_grid(grid)

    if _bonus_triggered(grid):
        session = DogslotSession(
            telegram_id=int(telegram_id),
            user_label=user_label,
            chat_id=int(chat_id),
            phase="bonus_pick",
        )
        return None, await _start_bonus_pick(conn, session, user_label, grid, base_points)

    await record_play(
        conn,
        telegram_id=int(telegram_id),
        user_label=user_label,
        game_type="dogslot",
        dice_value=max(
            (_line_payout([grid[r][c] for r, c in line])[1] for line in PAYLINES),
            default=0,
        ),
        points=base_points,
        meta={"mode": "base", "notes": notes},
    )
    rank = await get_user_rank(conn, telegram_id)

    lines = [
        _format_grid(grid),
        "",
    ]
    if notes:
        lines.extend(notes)
    else:
        lines.append("Без выигрыша на линиях.")
    lines.append(f"💎 +{base_points} очков")
    return None, _build_view(
        None,
        user_label,
        lines=lines,
        action="spin",
        can_act=True,
        rank=rank,
    )


async def dogslot_action(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    chat_id: int,
    action: Literal["spin", "pick", "free_spin"],
) -> tuple[Optional[str], Optional[DogslotView]]:
    session = await get_session(conn, telegram_id)
    if session is not None:
        if session.phase == "bonus_pick" and action == "spin":
            action = "pick"
        elif session.phase == "free_spins" and action in ("spin", "pick"):
            action = "free_spin"

    if action == "pick":
        return await run_bonus_pick(conn, telegram_id=telegram_id, user_label=user_label)
    if action == "free_spin":
        return await run_free_spin(conn, telegram_id=telegram_id, user_label=user_label)
    return await spin_dogslot(
        conn,
        telegram_id=telegram_id,
        user_label=user_label,
        chat_id=chat_id,
    )
