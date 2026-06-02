from __future__ import annotations

import asyncio
import secrets
from typing import Any, Optional

from aiogram import Bot
from aiogram.types import BufferedInputFile

from wheel_bot import db
from wheel_bot.render_wheel import render_multi_round_spin_media


class _SafeDict(dict[str, Any]):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def _fmt_template(template: str, **kwargs: Any) -> str:
    return str(template).format_map(_SafeDict(**kwargs))


def _participant_label(nick: str, description: str | None) -> str:
    d = str(description or "").strip()
    return f"{nick} ({d})" if d else str(nick)


def _announce_with_members(
    templates: dict[str, str],
    *,
    wheel_id: int,
    depositor_label: str,
    deposit_amount: float,
    prize_pool: float,
    prizes: list[float],
    roster_rows: list[tuple[int, str, str, int]],
    silent_footer: bool = False,
) -> str:
    prize_lines = [f"{idx}. ${float(prize):g}" for idx, prize in enumerate(prizes, start=1)]
    announce_text = _fmt_template(
        templates["announce"],
        wheel_id=wheel_id,
        depositor=depositor_label,
        deposit_amount=f"{float(deposit_amount):g}",
        prize_pool=f"{prize_pool:g}",
        winners_count=len(prizes),
        prize_lines="\n".join(prize_lines),
    )
    members_lines = [
        f"{idx}. {_participant_label(str(row[1]), str(row[2]))}" for idx, row in enumerate(roster_rows, start=1)
    ]
    text = (
        f"{announce_text}\n\n"
        f"👥 Участники текущего колеса:\n"
        f"{chr(10).join(members_lines)}"
    )
    if silent_footer:
        text += "\n\n🤫 Тишина в чате"
    return text


async def _prepare_spin_data(
    conn: Any,
    admin_telegram_id: int,
    depositor_id: int,
    deposit_amount: float,
    selected_ids: list[int],
    prizes: list[float],
) -> tuple[list[int], list[tuple[int, str, str, int]], dict[str, str], str, float]:
    if len(selected_ids) < 2:
        raise ValueError("Нужно минимум 2 участника в текущем колесе")
    if depositor_id not in selected_ids:
        raise ValueError("«Кто занёс» должен быть среди участников текущего колеса")
    if not prizes:
        raise ValueError("Укажите хотя бы одного победителя и суммы")
    if len(prizes) > len(selected_ids):
        raise ValueError("Победителей больше, чем участников")

    await db.ensure_user(conn, admin_telegram_id)

    roster_ids = list(dict.fromkeys(selected_ids))
    roster_rows: list[tuple[int, str, str, int]] = []
    for idx, pid in enumerate(roster_ids):
        p = await db.get_participant(conn, pid)
        if not p:
            raise ValueError(f"Участник id={pid} не найден")
        hue = int(idx * 360 / max(1, len(roster_ids))) % 360
        roster_rows.append((p.id, p.poker_nick, p.description, hue))

    depositor = await db.get_participant(conn, depositor_id)
    depositor_label = (
        _participant_label(depositor.poker_nick, depositor.description) if depositor else str(depositor_id)
    )
    templates = await db.get_message_templates(conn)
    prize_pool = float(sum(prizes))
    return roster_ids, roster_rows, templates, depositor_label, prize_pool


async def run_wheel_spin(
    *,
    conn: Any,
    bot: Bot,
    chat_id: int,
    admin_telegram_id: int,
    depositor_id: int,
    deposit_amount: float,
    selected_ids: list[int],
    prizes: list[float],
    announce_delay_sec: int = 30,
) -> dict[str, Any]:
    roster_ids, roster_rows, templates, depositor_label, prize_pool = await _prepare_spin_data(
        conn, admin_telegram_id, depositor_id, deposit_amount, selected_ids, prizes
    )

    db_roster = [(pid, idx, roster_rows[idx][3]) for idx, pid in enumerate(roster_ids)]

    sid = await db.create_wheel_session(
        conn,
        chat_id=chat_id,
        depositor_id=depositor_id,
        deposit_amount=float(deposit_amount),
        created_by=admin_telegram_id,
        roster=db_roster,
        mode="normal",
        results_sent=True,
    )
    delay_sec = max(0, min(300, int(announce_delay_sec)))
    announce_text = _announce_with_members(
        templates,
        wheel_id=sid,
        depositor_label=depositor_label,
        deposit_amount=float(deposit_amount),
        prize_pool=prize_pool,
        prizes=prizes,
        roster_rows=roster_rows,
        silent_footer=False,
    )
    await bot.send_message(chat_id, announce_text)
    if delay_sec:
        await asyncio.sleep(delay_sec)

    remaining_rows = roster_rows.copy()
    results: list[dict[str, Any]] = []
    gif_rounds: list[tuple[list[tuple[str, str, int]], int]] = []
    caption_lines: list[str] = []

    for rnd, prize in enumerate(prizes, start=1):
        if not remaining_rows:
            raise RuntimeError("Некому выбирать победителя")
        winner_row = secrets.choice(remaining_rows)
        winner_id = int(winner_row[0])

        await db.add_spin(conn, sid, rnd, winner_id, float(prize))

        winner_slot = next(i for i, row in enumerate(remaining_rows) if int(row[0]) == winner_id)
        gif_roster = [(row[1], row[2], row[3]) for row in remaining_rows]
        gif_rounds.append((gif_roster, winner_slot))

        pwin = await db.get_participant(conn, winner_id)
        nick = _participant_label(pwin.poker_nick, pwin.description) if pwin else str(winner_id)
        caption_lines.append(
            _fmt_template(
                templates["round_caption"],
                round=rnd,
                winner=nick,
                prize=f"{float(prize):g}",
                wheel_id=sid,
            )
        )

        results.append({"round": rnd, "winner_id": winner_id, "prize": float(prize)})
        remaining_rows = [row for row in remaining_rows if int(row[0]) != winner_id]

    media_bytes, media_ext = render_multi_round_spin_media(gif_rounds)
    caption = "\n".join(caption_lines)
    if len(caption) > 1020:
        caption = caption[:1017] + "…"
    await bot.send_animation(
        chat_id,
        BufferedInputFile(media_bytes, filename=f"wheel_{sid}.{media_ext}"),
        caption=caption or f"🎡 Колесо #{sid}",
    )

    await db.set_last_roster_ids(conn, roster_ids)
    await db.set_draft_ids(conn, roster_ids)

    summary_lines = []
    for item in results:
        pw = await db.get_participant(conn, int(item["winner_id"]))
        nn = _participant_label(pw.poker_nick, pw.description) if pw else str(item["winner_id"])
        summary_lines.append(f"— {nn}: ${item['prize']:g}")
    finish_text = _fmt_template(
        templates["finish"],
        wheel_id=sid,
        depositor=depositor_label,
        deposit_amount=f"{float(deposit_amount):g}",
        winner_lines="\n".join(summary_lines) if summary_lines else "— нет победителей",
    )
    await bot.send_message(chat_id, finish_text)

    return {"session_id": sid, "results": results}


async def run_wheel_spin_silent(
    *,
    conn: Any,
    bot: Bot,
    chat_id: int,
    admin_telegram_id: int,
    depositor_id: int,
    deposit_amount: float,
    selected_ids: list[int],
    prizes: list[float],
    session_id: Optional[int] = None,
) -> dict[str, Any]:
    roster_ids, roster_rows, templates, depositor_label, prize_pool = await _prepare_spin_data(
        conn, admin_telegram_id, depositor_id, deposit_amount, selected_ids, prizes
    )
    db_roster = [(pid, idx, roster_rows[idx][3]) for idx, pid in enumerate(roster_ids)]
    if session_id is None:
        sid = await db.create_wheel_session(
            conn,
            chat_id=chat_id,
            depositor_id=depositor_id,
            deposit_amount=float(deposit_amount),
            created_by=admin_telegram_id,
            roster=db_roster,
            mode="silent",
            results_sent=False,
        )
    else:
        session = await db.get_wheel_session(conn, int(session_id))
        if not session or int(session["chat_id"]) != int(chat_id):
            raise ValueError("Колесо не найдено")
        if str(session["mode"]) != "silent":
            raise ValueError("Это колесо не в режиме тишины")
        existing = await db.list_session_winners(conn, int(session_id))
        if existing:
            raise ValueError("Для этого колеса уже есть результаты")
        sid = int(session_id)

    remaining_rows = roster_rows.copy()
    rounds: list[dict[str, Any]] = []
    for rnd, prize in enumerate(prizes, start=1):
        if not remaining_rows:
            raise RuntimeError("Некому выбирать победителя")
        round_roster = [{"id": int(r[0]), "nick": str(r[1]), "description": str(r[2]), "hue": int(r[3])} for r in remaining_rows]
        winner_row = secrets.choice(remaining_rows)
        winner_id = int(winner_row[0])
        await db.add_spin(conn, sid, rnd, winner_id, float(prize))
        rounds.append(
            {
                "round": rnd,
                "prize": float(prize),
                "winner_id": winner_id,
                "winner_nick": _participant_label(str(winner_row[1]), str(winner_row[2])),
                "roster": round_roster,
            }
        )
        remaining_rows = [row for row in remaining_rows if int(row[0]) != winner_id]

    await db.set_last_roster_ids(conn, roster_ids)
    await db.set_draft_ids(conn, roster_ids)

    return {"session_id": sid, "rounds": rounds}


async def send_silent_announce(
    *,
    conn: Any,
    bot: Bot,
    chat_id: int,
    admin_telegram_id: int,
    depositor_id: int,
    deposit_amount: float,
    selected_ids: list[int],
    prizes: list[float],
) -> dict[str, Any]:
    roster_ids, roster_rows, templates, depositor_label, prize_pool = await _prepare_spin_data(
        conn, admin_telegram_id, depositor_id, deposit_amount, selected_ids, prizes
    )
    db_roster = [(pid, idx, roster_rows[idx][3]) for idx, pid in enumerate(roster_ids)]
    sid = await db.create_wheel_session(
        conn,
        chat_id=chat_id,
        depositor_id=depositor_id,
        deposit_amount=float(deposit_amount),
        created_by=admin_telegram_id,
        roster=db_roster,
        mode="silent",
        results_sent=False,
    )
    await db.set_last_roster_ids(conn, roster_ids)
    await db.set_draft_ids(conn, roster_ids)
    text = _announce_with_members(
        templates,
        wheel_id=sid,
        depositor_label=depositor_label,
        deposit_amount=float(deposit_amount),
        prize_pool=prize_pool,
        prizes=prizes,
        roster_rows=roster_rows,
        silent_footer=True,
    )
    await bot.send_message(chat_id, text)
    return {"ok": True, "session_id": sid}


async def send_silent_results(
    *,
    conn: Any,
    bot: Bot,
    chat_id: int,
    session_id: int,
) -> dict[str, Any]:
    session = await db.get_wheel_session(conn, session_id)
    if not session or int(session["chat_id"]) != int(chat_id):
        raise ValueError("Колесо не найдено")
    if str(session["mode"]) != "silent":
        raise ValueError("Это колесо не в режиме тишины")
    if bool(session["results_sent"]):
        raise ValueError("Результаты этого колеса уже отправлены в чат")

    winners = await db.list_session_winners(conn, session_id)
    depositor = await db.get_participant(conn, int(session["depositor_id"]))
    depositor_label = (
        _participant_label(depositor.poker_nick, depositor.description) if depositor else str(session["depositor_id"])
    )
    templates = await db.get_message_templates(conn)

    winner_lines = [f"— {str(w['winner_label'])}: ${float(w['prize']):g}" for w in winners]
    finish_text = _fmt_template(
        templates["finish"],
        wheel_id=int(session["id"]),
        depositor=depositor_label,
        deposit_amount=f"{float(session['deposit_amount']):g}",
        winner_lines="\n".join(winner_lines) if winner_lines else "— нет победителей",
    )
    await bot.send_message(chat_id, finish_text)
    await db.mark_wheel_session_results_sent(conn, session_id)
    return {"ok": True, "session_id": int(session["id"])}
