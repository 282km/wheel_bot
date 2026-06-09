from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite
from typing import Optional

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    role TEXT NOT NULL CHECK(role IN ('user','admin','superadmin')),
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    poker_nick TEXT NOT NULL COLLATE NOCASE,
    description TEXT NOT NULL DEFAULT '',
    is_hidden INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    UNIQUE(poker_nick)
);

CREATE TABLE IF NOT EXISTS wheel_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    depositor_id INTEGER NOT NULL REFERENCES participants(id),
    deposit_amount REAL NOT NULL,
    created_by INTEGER NOT NULL REFERENCES users(telegram_id),
    mode TEXT NOT NULL DEFAULT 'normal',
    results_sent INTEGER NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_sessions_chat_time ON wheel_sessions(chat_id, created_at);

CREATE TABLE IF NOT EXISTS wheel_session_roster (
    session_id INTEGER NOT NULL REFERENCES wheel_sessions(id) ON DELETE CASCADE,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    slot_index INTEGER NOT NULL,
    hue INTEGER NOT NULL,
    PRIMARY KEY(session_id, participant_id)
);

CREATE TABLE IF NOT EXISTS wheel_spins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES wheel_sessions(id) ON DELETE CASCADE,
    round_index INTEGER NOT NULL,
    winner_id INTEGER NOT NULL REFERENCES participants(id),
    prize_amount REAL NOT NULL,
    UNIQUE(session_id, round_index)
);

CREATE TABLE IF NOT EXISTS app_kv (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass
class ParticipantRow:
    id: int
    poker_nick: str
    description: str
    is_hidden: bool


async def connect(db_path: Path) -> aiosqlite.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(db_path)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(SCHEMA)
    # Migration for existing databases created before is_hidden field.
    cols = await (await conn.execute("PRAGMA table_info(participants)")).fetchall()
    names = {str(r["name"]) for r in cols}
    if "is_hidden" not in names:
        await conn.execute("ALTER TABLE participants ADD COLUMN is_hidden INTEGER NOT NULL DEFAULT 0")
    ws_cols = await (await conn.execute("PRAGMA table_info(wheel_sessions)")).fetchall()
    ws_names = {str(r["name"]) for r in ws_cols}
    if "mode" not in ws_names:
        await conn.execute("ALTER TABLE wheel_sessions ADD COLUMN mode TEXT NOT NULL DEFAULT 'normal'")
    if "results_sent" not in ws_names:
        await conn.execute("ALTER TABLE wheel_sessions ADD COLUMN results_sent INTEGER NOT NULL DEFAULT 1")
    await conn.commit()
    return conn


async def bootstrap_users(conn: aiosqlite.Connection, superadmin_ids: set[int]) -> None:
    now = utc_now_iso()
    for tg_id in superadmin_ids:
        await conn.execute(
            """
            INSERT INTO users (telegram_id, role, created_at)
            VALUES (?, 'superadmin', ?)
            ON CONFLICT(telegram_id) DO UPDATE SET role = 'superadmin'
            """,
            (tg_id, now),
        )
    await conn.commit()


async def ensure_user(conn: aiosqlite.Connection, telegram_id: int, role: Optional[str] = None) -> str:
    row = await (await conn.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,))).fetchone()
    if row:
        return str(row["role"])
    now = utc_now_iso()
    r = role or "user"
    await conn.execute(
        "INSERT INTO users (telegram_id, role, created_at) VALUES (?, ?, ?)",
        (telegram_id, r, now),
    )
    await conn.commit()
    return r


async def get_role(conn: aiosqlite.Connection, telegram_id: int) -> Optional[str]:
    row = await (await conn.execute("SELECT role FROM users WHERE telegram_id = ?", (telegram_id,))).fetchone()
    return str(row["role"]) if row else None


async def set_role(conn: aiosqlite.Connection, telegram_id: int, role: str) -> None:
    now = utc_now_iso()
    await conn.execute(
        """
        INSERT INTO users (telegram_id, role, created_at) VALUES (?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET role = excluded.role
        """,
        (telegram_id, role, now),
    )
    await conn.commit()


async def list_admins(conn: aiosqlite.Connection) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "SELECT telegram_id, role FROM users WHERE role IN ('admin','superadmin') ORDER BY telegram_id ASC"
    )
    rows = await cur.fetchall()
    return [{"telegram_id": int(r["telegram_id"]), "role": str(r["role"])} for r in rows]


async def list_participants(conn: aiosqlite.Connection, include_hidden: bool = True) -> list[ParticipantRow]:
    where = "" if include_hidden else "WHERE is_hidden = 0"
    cur = await conn.execute(
        f"SELECT id, poker_nick, description, is_hidden FROM participants {where} ORDER BY poker_nick COLLATE NOCASE ASC"
    )
    rows = await cur.fetchall()
    return [
        ParticipantRow(int(r["id"]), str(r["poker_nick"]), str(r["description"]), bool(int(r["is_hidden"])))
        for r in rows
    ]


async def insert_participant(conn: aiosqlite.Connection, poker_nick: str, description: str) -> int:
    now = utc_now_iso()
    cur = await conn.execute(
        "INSERT INTO participants (poker_nick, description, created_at) VALUES (?, ?, ?)",
        (poker_nick.strip(), description.strip(), now),
    )
    await conn.commit()
    return int(cur.lastrowid)


async def update_participant(
    conn: aiosqlite.Connection,
    pid: int,
    poker_nick: Optional[str],
    description: Optional[str],
    is_hidden: Optional[bool] = None,
) -> None:
    fields: list[str] = []
    vals: list[Any] = []
    if poker_nick is not None:
        fields.append("poker_nick = ?")
        vals.append(poker_nick.strip())
    if description is not None:
        fields.append("description = ?")
        vals.append(description.strip())
    if is_hidden is not None:
        fields.append("is_hidden = ?")
        vals.append(1 if is_hidden else 0)
    if not fields:
        return
    vals.append(pid)
    await conn.execute(f"UPDATE participants SET {', '.join(fields)} WHERE id = ?", vals)
    await conn.commit()


async def participant_used_in_history(conn: aiosqlite.Connection, pid: int) -> bool:
    row = await (
        await conn.execute(
            """
            SELECT 1
            WHERE EXISTS(SELECT 1 FROM wheel_session_roster WHERE participant_id = ?)
               OR EXISTS(SELECT 1 FROM wheel_spins WHERE winner_id = ?)
               OR EXISTS(SELECT 1 FROM wheel_sessions WHERE depositor_id = ?)
            """,
            (pid, pid, pid),
        )
    ).fetchone()
    return row is not None


async def delete_participant(conn: aiosqlite.Connection, pid: int) -> bool:
    ok = not await participant_used_in_history(conn, pid)
    if not ok:
        return False
    await conn.execute("DELETE FROM participants WHERE id = ?", (pid,))
    await conn.commit()
    return True


async def get_kv(conn: aiosqlite.Connection, key: str, default: Optional[str] = None) -> Optional[str]:
    row = await (await conn.execute("SELECT value FROM app_kv WHERE key = ?", (key,))).fetchone()
    if not row:
        return default
    return str(row["value"])


async def set_kv(conn: aiosqlite.Connection, key: str, value: str) -> None:
    await conn.execute(
        "INSERT INTO app_kv(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    await conn.commit()


async def get_draft_ids(conn: aiosqlite.Connection) -> list[int]:
    raw = await get_kv(conn, "wheel_draft_ids", "[]")
    try:
        data = json.loads(raw or "[]")
        return [int(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


async def set_draft_ids(conn: aiosqlite.Connection, ids: list[int]) -> None:
    await set_kv(conn, "wheel_draft_ids", json.dumps(ids))


async def get_last_roster_ids(conn: aiosqlite.Connection) -> list[int]:
    raw = await get_kv(conn, "last_roster_ids", "[]")
    try:
        data = json.loads(raw or "[]")
        return [int(x) for x in data]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


async def set_last_roster_ids(conn: aiosqlite.Connection, ids: list[int]) -> None:
    await set_kv(conn, "last_roster_ids", json.dumps(ids))


async def create_wheel_session(
    conn: aiosqlite.Connection,
    chat_id: int,
    depositor_id: int,
    deposit_amount: float,
    created_by: int,
    roster: list[tuple[int, int, int]],
    mode: str = "normal",
    results_sent: bool = True,
) -> int:
    """
    roster: list of (participant_id, slot_index, hue)
    """
    now = utc_now_iso()
    cur = await conn.execute(
        """
        INSERT INTO wheel_sessions (chat_id, created_at, depositor_id, deposit_amount, created_by, mode, results_sent)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (chat_id, now, depositor_id, float(deposit_amount), created_by, str(mode), 1 if results_sent else 0),
    )
    await conn.commit()
    sid = int(cur.lastrowid)
    for participant_id, slot_index, hue in roster:
        await conn.execute(
            "INSERT INTO wheel_session_roster(session_id, participant_id, slot_index, hue) VALUES (?,?,?,?)",
            (sid, participant_id, slot_index, hue),
        )
    await conn.commit()
    return sid


async def add_spin(conn: aiosqlite.Connection, session_id: int, round_index: int, winner_id: int, prize: float) -> None:
    await conn.execute(
        """
        INSERT INTO wheel_spins(session_id, round_index, winner_id, prize_amount)
        VALUES (?,?,?,?)
        """,
        (session_id, round_index, winner_id, float(prize)),
    )
    await conn.commit()


async def get_participant(conn: aiosqlite.Connection, pid: int) -> Optional[ParticipantRow]:
    row = await (
        await conn.execute("SELECT id, poker_nick, description, is_hidden FROM participants WHERE id = ?", (pid,))
    ).fetchone()
    if not row:
        return None
    return ParticipantRow(int(row["id"]), str(row["poker_nick"]), str(row["description"]), bool(int(row["is_hidden"])))


async def fetch_roster_participants(
    conn: aiosqlite.Connection, session_id: int
) -> list[tuple[int, str, str, int, int]]:
    cur = await conn.execute(
        """
        SELECT p.id, p.poker_nick, p.description, r.slot_index, r.hue
        FROM wheel_session_roster r
        JOIN participants p ON p.id = r.participant_id
        WHERE r.session_id = ?
        ORDER BY r.slot_index ASC
        """,
        (session_id,),
    )
    rows = await cur.fetchall()
    return [(int(r["id"]), str(r["poker_nick"]), str(r["description"]), int(r["slot_index"]), int(r["hue"])) for r in rows]


async def list_wheel_history(conn: aiosqlite.Connection, chat_id: int, limit: int = 200) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT
            s.id,
            s.created_at,
            s.deposit_amount,
            d.poker_nick AS depositor_nick,
            COUNT(w.id) AS winners_count,
            COALESCE(SUM(w.prize_amount), 0) AS prizes_sum
        FROM wheel_sessions s
        JOIN participants d ON d.id = s.depositor_id
        LEFT JOIN wheel_spins w ON w.session_id = s.id
        WHERE s.chat_id = ?
        GROUP BY s.id, s.created_at, s.deposit_amount, d.poker_nick
        ORDER BY s.id DESC
        LIMIT ?
        """,
        (chat_id, int(limit)),
    )
    rows = await cur.fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        sid = int(r["id"])
        winners_cur = await conn.execute(
            """
            SELECT p.poker_nick AS nick, w.prize_amount AS prize
            FROM wheel_spins w
            JOIN participants p ON p.id = w.winner_id
            WHERE w.session_id = ?
            ORDER BY w.round_index ASC
            """,
            (sid,),
        )
        winners_rows = await winners_cur.fetchall()
        winners = [{"nick": str(wr["nick"]), "prize": float(wr["prize"])} for wr in winners_rows]
        out.append(
            {
                "id": sid,
                "created_at": str(r["created_at"]),
                "depositor_nick": str(r["depositor_nick"]),
                "deposit_amount": float(r["deposit_amount"]),
                "winners_count": int(r["winners_count"]),
                "prizes_sum": float(r["prizes_sum"]),
                "winners": winners,
            }
        )
    return out


async def get_open_silent_session(conn: aiosqlite.Connection, chat_id: int) -> Optional[dict[str, Any]]:
    """
    Незавершённое тихое колесо: results_sent=0.
    has_winners=True — уже крутили, ждут «отправить результаты».
    """
    row = await (
        await conn.execute(
            """
            SELECT s.id,
                   (SELECT COUNT(*) FROM wheel_spins w WHERE w.session_id = s.id) AS spin_count
            FROM wheel_sessions s
            WHERE s.chat_id = ? AND s.mode = 'silent' AND s.results_sent = 0
            ORDER BY s.id DESC
            LIMIT 1
            """,
            (int(chat_id),),
        )
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "has_winners": int(row["spin_count"]) > 0,
    }


async def get_wheel_session(conn: aiosqlite.Connection, session_id: int) -> Optional[dict[str, Any]]:
    row = await (
        await conn.execute(
            """
            SELECT id, chat_id, depositor_id, deposit_amount, mode, results_sent
            FROM wheel_sessions
            WHERE id = ?
            """,
            (int(session_id),),
        )
    ).fetchone()
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "chat_id": int(row["chat_id"]),
        "depositor_id": int(row["depositor_id"]),
        "deposit_amount": float(row["deposit_amount"]),
        "mode": str(row["mode"]),
        "results_sent": bool(int(row["results_sent"])),
    }


async def list_session_winners(conn: aiosqlite.Connection, session_id: int) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        SELECT w.round_index, w.winner_id, w.prize_amount, p.poker_nick, p.description
        FROM wheel_spins w
        JOIN participants p ON p.id = w.winner_id
        WHERE w.session_id = ?
        ORDER BY w.round_index ASC
        """,
        (int(session_id),),
    )
    rows = await cur.fetchall()
    return [
        {
            "round": int(r["round_index"]),
            "winner_id": int(r["winner_id"]),
            "winner_nick": str(r["poker_nick"]),
            "winner_label": (
                f"{str(r['poker_nick'])} ({str(r['description']).strip()})"
                if str(r["description"]).strip()
                else str(r["poker_nick"])
            ),
            "prize": float(r["prize_amount"]),
        }
        for r in rows
    ]


async def mark_wheel_session_results_sent(conn: aiosqlite.Connection, session_id: int) -> None:
    await conn.execute("UPDATE wheel_sessions SET results_sent = 1 WHERE id = ?", (int(session_id),))
    await conn.commit()


async def delete_wheel_session(conn: aiosqlite.Connection, session_id: int) -> bool:
    """Удаляет одно колесо и связанные roster/spins (CASCADE)."""
    sid = int(session_id)
    row = await (
        await conn.execute("SELECT id FROM wheel_sessions WHERE id = ?", (sid,))
    ).fetchone()
    if not row:
        return False
    await conn.execute("DELETE FROM wheel_sessions WHERE id = ?", (sid,))
    await conn.commit()
    return True


async def renumber_wheel_session(
    conn: aiosqlite.Connection,
    from_id: int,
    to_id: int,
    *,
    replace_target: bool = False,
) -> None:
    """Переносит колесо from_id -> to_id (roster, spins, запись сессии)."""
    src_id = int(from_id)
    dst_id = int(to_id)
    if src_id == dst_id:
        return
    if not await get_wheel_session(conn, src_id):
        raise ValueError(f"Колесо #{src_id} не найдено")
    if await get_wheel_session(conn, dst_id):
        if not replace_target:
            raise ValueError(f"Колесо #{dst_id} уже существует (используйте replace_target=True)")
        await delete_wheel_session(conn, dst_id)

    await conn.execute("PRAGMA foreign_keys=OFF")
    try:
        await conn.execute(
            "UPDATE wheel_session_roster SET session_id = ? WHERE session_id = ?",
            (dst_id, src_id),
        )
        await conn.execute(
            "UPDATE wheel_spins SET session_id = ? WHERE session_id = ?",
            (dst_id, src_id),
        )
        await conn.execute(
            "UPDATE wheel_sessions SET id = ? WHERE id = ?",
            (dst_id, src_id),
        )
        await conn.commit()
    finally:
        await conn.execute("PRAGMA foreign_keys=ON")


async def sync_wheel_session_autoincrement(conn: aiosqlite.Connection) -> int:
    """Выставляет AUTOINCREMENT так, чтобы следующее колесо было max(id)+1."""
    row = await (await conn.execute("SELECT COALESCE(MAX(id), 0) AS m FROM wheel_sessions")).fetchone()
    max_id = int(row["m"])
    await conn.execute("DELETE FROM sqlite_sequence WHERE name = 'wheel_sessions'")
    if max_id > 0:
        await conn.execute(
            "INSERT INTO sqlite_sequence (name, seq) VALUES ('wheel_sessions', ?)",
            (max_id,),
        )
    await conn.commit()
    return max_id + 1


MESSAGE_TEMPLATE_DEFAULTS: dict[str, str] = {
    "announce": (
        "🎡 Стартует колесо #{wheel_id}\n"
        "💸 Кто занёс: {depositor}\n"
        "💰 Призовой фонд колеса: ${prize_pool}\n"
        "🏆 Количество победителей: {winners_count}\n"
        "📋 Суммы по победителям:\n"
        "{prize_lines}\n"
        "🍀 Удачи всем участникам! ♣️"
    ),
    "round_caption": "🎯 Раунд {round}: {winner} — приз ${prize} 💵🍀",
    "finish": (
        "✅ Колесо #{wheel_id} завершено!\n"
        "💸 Занёс: {depositor}\n"
        "🏆 Победители:\n"
        "{winner_lines}\n\n"
        "🤝 Спасибо за колесо, {depositor}!\n"
        "🍀♣️ Всем удачи за столами!"
    ),
}


async def get_message_templates(conn: aiosqlite.Connection) -> dict[str, str]:
    out = dict(MESSAGE_TEMPLATE_DEFAULTS)
    cur = await conn.execute("SELECT key, value FROM app_kv WHERE key LIKE 'msg_tpl_%'")
    rows = await cur.fetchall()
    for r in rows:
        key = str(r["key"]).replace("msg_tpl_", "", 1)
        if key in out:
            out[key] = str(r["value"])
    return out


async def set_message_templates(conn: aiosqlite.Connection, templates: dict[str, str]) -> None:
    for key, value in templates.items():
        if key not in MESSAGE_TEMPLATE_DEFAULTS:
            continue
        await set_kv(conn, f"msg_tpl_{key}", str(value))


async def reset_message_templates(conn: aiosqlite.Connection) -> None:
    for key, value in MESSAGE_TEMPLATE_DEFAULTS.items():
        await set_kv(conn, f"msg_tpl_{key}", value)


async def reset_all_wheel_data(conn: aiosqlite.Connection) -> dict[str, int]:
    """
    Полный сброс данных колеса: участники, история, драфт, шаблоны в app_kv.
    Таблица users (роли admin/superadmin) не трогается.
    """
    stats: dict[str, int] = {}

    async def _count(table: str) -> int:
        row = await (await conn.execute(f"SELECT COUNT(*) AS c FROM {table}")).fetchone()
        return int(row["c"])

    stats["participants"] = await _count("participants")
    stats["wheel_sessions"] = await _count("wheel_sessions")
    stats["wheel_spins"] = await _count("wheel_spins")
    stats["wheel_session_roster"] = await _count("wheel_session_roster")
    kv_row = await (await conn.execute("SELECT COUNT(*) AS c FROM app_kv")).fetchone()
    stats["app_kv"] = int(kv_row["c"])

    await conn.execute("DELETE FROM wheel_spins")
    await conn.execute("DELETE FROM wheel_session_roster")
    await conn.execute("DELETE FROM wheel_sessions")
    await conn.execute("DELETE FROM participants")
    await conn.execute("DELETE FROM app_kv")
    await conn.execute(
        "DELETE FROM sqlite_sequence WHERE name IN ('participants', 'wheel_sessions', 'wheel_spins')"
    )
    await conn.commit()
    return stats
