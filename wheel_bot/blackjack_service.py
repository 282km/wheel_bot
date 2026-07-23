from __future__ import annotations

import json
import secrets
from dataclasses import dataclass
from typing import Any, Literal, Optional

import aiosqlite

from wheel_bot.db import utc_now_iso
from wheel_bot.game_service import (
    get_user_rank,
    record_play,
)

Outcome = Literal["blackjack", "win", "push", "loss", "bust"]

SUITS = ("♠", "♥", "♦", "♣")
RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")

BLACKJACK_SCHEMA = """
CREATE TABLE IF NOT EXISTS blackjack_sessions (
    telegram_id INTEGER PRIMARY KEY,
    user_label TEXT NOT NULL DEFAULT '',
    chat_id INTEGER NOT NULL,
    message_id INTEGER,
    deck_json TEXT NOT NULL,
    player_json TEXT NOT NULL,
    dealer_json TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


async def _migrate_blackjack_sessions(conn: aiosqlite.Connection) -> None:
    cur = await conn.execute("PRAGMA table_info(blackjack_sessions)")
    cols = {str(row[1]) for row in await cur.fetchall()}
    if cols and "message_id" not in cols:
        await conn.execute("ALTER TABLE blackjack_sessions ADD COLUMN message_id INTEGER")


async def ensure_blackjack_schema(conn: aiosqlite.Connection) -> None:
    await conn.executescript(BLACKJACK_SCHEMA)
    await _migrate_blackjack_sessions(conn)
    await conn.commit()


def _new_deck() -> list[str]:
    return [f"{rank}{suit}" for suit in SUITS for rank in RANKS]


def _shuffle_deck() -> list[str]:
    deck = _new_deck()
    secrets.SystemRandom().shuffle(deck)
    return deck


def _rank_of(card: str) -> str:
    return card[:-1]


def _hand_totals(cards: list[str]) -> tuple[int, int]:
    """Returns (hard_total, best_total <= 21 or hard_total if bust)."""
    total = 0
    aces = 0
    for card in cards:
        rank = _rank_of(card)
        if rank == "A":
            aces += 1
            total += 11
        elif rank in ("J", "Q", "K"):
            total += 10
        else:
            total += int(rank)
    hard = total
    while total > 21 and aces > 0:
        total -= 10
        aces -= 1
    return hard, total


def hand_value(cards: list[str]) -> int:
    return _hand_totals(cards)[1]


def is_natural_blackjack(cards: list[str]) -> bool:
    return len(cards) == 2 and hand_value(cards) == 21


def format_cards(cards: list[str], *, hide_tail: bool = False) -> str:
    if not cards:
        return "—"
    if hide_tail and len(cards) >= 2:
        visible = " ".join(cards[:1] + ["🂠"])
        return visible
    return " ".join(cards)


def score_outcome(outcome: Outcome) -> tuple[int, str]:
    if outcome == "blackjack":
        return 70, "BLACKJACK! 🃏✨ Натуральная 21 — красиво!"
    if outcome == "win":
        return 40, "Победа! Дилер проиграл."
    if outcome == "push":
        return 15, "Ничья — push, ставка возвращается."
    if outcome == "bust":
        return 5, "Перебор — банк дилеру."
    return 5, "Не повезло — в следующий раз!"


@dataclass
class BlackjackSession:
    telegram_id: int
    user_label: str
    chat_id: int
    deck: list[str]
    player: list[str]
    dealer: list[str]
    message_id: Optional[int] = None


@dataclass
class BlackjackView:
    text: str
    finished: bool
    outcome: Optional[Outcome] = None
    points: int = 0
    flair: str = ""


async def get_session(conn: aiosqlite.Connection, telegram_id: int) -> Optional[BlackjackSession]:
    row = await (
        await conn.execute(
            """
            SELECT telegram_id, user_label, chat_id, message_id, deck_json, player_json, dealer_json
            FROM blackjack_sessions WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    ).fetchone()
    if not row:
        return None
    return BlackjackSession(
        telegram_id=int(row["telegram_id"]),
        user_label=str(row["user_label"]),
        chat_id=int(row["chat_id"]),
        deck=json.loads(str(row["deck_json"])),
        player=json.loads(str(row["player_json"])),
        dealer=json.loads(str(row["dealer_json"])),
        message_id=int(row["message_id"]) if row["message_id"] is not None else None,
    )


async def save_session(conn: aiosqlite.Connection, session: BlackjackSession) -> None:
    await conn.execute(
        """
        INSERT INTO blackjack_sessions
            (telegram_id, user_label, chat_id, message_id, deck_json, player_json, dealer_json, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            user_label = excluded.user_label,
            chat_id = excluded.chat_id,
            message_id = excluded.message_id,
            deck_json = excluded.deck_json,
            player_json = excluded.player_json,
            dealer_json = excluded.dealer_json,
            updated_at = excluded.updated_at
        """,
        (
            session.telegram_id,
            session.user_label,
            session.chat_id,
            session.message_id,
            json.dumps(session.deck, ensure_ascii=False),
            json.dumps(session.player, ensure_ascii=False),
            json.dumps(session.dealer, ensure_ascii=False),
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
        UPDATE blackjack_sessions
        SET message_id = ?, updated_at = ?
        WHERE telegram_id = ?
        """,
        (int(message_id), utc_now_iso(), int(telegram_id)),
    )
    await conn.commit()


async def clear_session(conn: aiosqlite.Connection, telegram_id: int) -> None:
    await conn.execute("DELETE FROM blackjack_sessions WHERE telegram_id = ?", (int(telegram_id),))
    await conn.commit()


def _draw(session: BlackjackSession, target: Literal["player", "dealer"]) -> None:
    if not session.deck:
        session.deck = _shuffle_deck()
    card = session.deck.pop()
    if target == "player":
        session.player.append(card)
    else:
        session.dealer.append(card)


def _dealer_play(session: BlackjackSession) -> None:
    while hand_value(session.dealer) < 17:
        _draw(session, "dealer")


def _resolve_outcome(session: BlackjackSession) -> Outcome:
    p = hand_value(session.player)
    d = hand_value(session.dealer)
    if p > 21:
        return "bust"
    if is_natural_blackjack(session.player) and not is_natural_blackjack(session.dealer):
        return "blackjack"
    if is_natural_blackjack(session.dealer) and not is_natural_blackjack(session.player):
        return "loss"
    if is_natural_blackjack(session.player) and is_natural_blackjack(session.dealer):
        return "push"
    _dealer_play(session)
    d = hand_value(session.dealer)
    p = hand_value(session.player)
    if p > 21:
        return "bust"
    if d > 21:
        return "win"
    if p > d:
        return "win"
    if p == d:
        return "push"
    return "loss"


def _format_active(session: BlackjackSession, user_label: str) -> str:
    p_val = hand_value(session.player)
    lines = [
        f"🃏 *Блэкджек* — {user_label}",
        "",
        f"{user_label} ({p_val}): {format_cards(session.player)}",
        f"Дилер: {format_cards(session.dealer, hide_tail=True)}",
        "",
        f"👆 Кнопки ниже — только для {user_label}",
    ]
    return "\n".join(lines)


def _format_result(session: BlackjackSession, user_label: str, outcome: Outcome, flair: str, points: int) -> str:
    p_val = hand_value(session.player)
    d_val = hand_value(session.dealer)
    lines = [
        f"🃏 *Блэкджек* — {user_label}",
        "",
        f"{user_label} ({p_val}): {format_cards(session.player)}",
        f"Дилер ({d_val}): {format_cards(session.dealer)}",
        "",
        flair,
        f"💎 +{points} очков",
    ]
    return "\n".join(lines)


async def start_blackjack(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
    chat_id: int,
) -> tuple[Optional[str], Optional[BlackjackView]]:
    existing = await get_session(conn, telegram_id)
    if existing is not None:
        return (
            f"У вас уже идёт партия у сообщения выше. "
            f"Кнопки Hit/Stand — только на вашей доске ({existing.user_label}).",
            None,
        )

    session = BlackjackSession(
        telegram_id=int(telegram_id),
        user_label=user_label,
        chat_id=int(chat_id),
        deck=_shuffle_deck(),
        player=[],
        dealer=[],
    )
    _draw(session, "player")
    _draw(session, "dealer")
    _draw(session, "player")
    _draw(session, "dealer")

    if is_natural_blackjack(session.player) or is_natural_blackjack(session.dealer):
        view = await _finish_session(conn, session, user_label)
        return None, view

    await save_session(conn, session)
    return None, BlackjackView(text=_format_active(session, user_label), finished=False)


async def hit_blackjack(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
) -> tuple[Optional[str], Optional[BlackjackView]]:
    session = await get_session(conn, telegram_id)
    if session is None:
        return "Нет активной партии. Начните: `/blackjack`", None

    _draw(session, "player")
    if hand_value(session.player) > 21:
        view = await _finish_session(conn, session, user_label)
        return None, view

    await save_session(conn, session)
    return None, BlackjackView(text=_format_active(session, user_label), finished=False)


async def stand_blackjack(
    conn: aiosqlite.Connection,
    *,
    telegram_id: int,
    user_label: str,
) -> tuple[Optional[str], Optional[BlackjackView]]:
    session = await get_session(conn, telegram_id)
    if session is None:
        return "Нет активной партии. Начните: `/blackjack`", None

    view = await _finish_session(conn, session, user_label)
    return None, view


async def _finish_session(
    conn: aiosqlite.Connection,
    session: BlackjackSession,
    user_label: str,
) -> BlackjackView:
    outcome = _resolve_outcome(session)
    points, flair = score_outcome(outcome)
    player_total = hand_value(session.player)
    dealer_total = hand_value(session.dealer)

    await record_play(
        conn,
        telegram_id=session.telegram_id,
        user_label=user_label,
        game_type="blackjack",
        dice_value=player_total,
        points=points,
        meta={
            "outcome": outcome,
            "player_total": player_total,
            "dealer_total": dealer_total,
            "player_cards": session.player,
            "dealer_cards": session.dealer,
        },
    )
    await clear_session(conn, session.telegram_id)
    rank = await get_user_rank(conn, session.telegram_id)

    text = _format_result(session, user_label, outcome, flair, points)
    if rank is not None:
        text += f"\n🏆 Место в недельном топе: {rank}-е"
    text += "\n\n📊 Топ: `/games`"

    return BlackjackView(
        text=text,
        finished=True,
        outcome=outcome,
        points=points,
        flair=flair,
    )
