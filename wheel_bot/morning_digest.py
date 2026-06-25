from __future__ import annotations

import asyncio
import json
import logging
import urllib.request
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from wheel_bot import db
from wheel_bot.morning_digest_settings import MorningDigestConfig, load_morning_digest_config

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.morning_digest")

MORNING_DIGEST_KV_KEY = "morning_digest_last_date"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"


def _msk_now(cfg: MorningDigestConfig) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone))


def _today_key(cfg: MorningDigestConfig, when: Optional[datetime] = None) -> str:
    dt = when or _msk_now(cfg)
    return dt.date().isoformat()


def _build_prompt(cfg: MorningDigestConfig, when: datetime) -> str:
    day = when.strftime("%d.%m.%Y")
    month_day = when.strftime("%d %B")
    return (
        f"Сегодня {day} ({month_day}) по календарю.\n\n"
        "Напиши одно утреннее сообщение для покерного Telegram-чата на русском языке.\n"
        "Структура:\n"
        "1) Короткое «доброе утро» с 1–2 уместными эмодзи.\n"
        "2) Один интересный блок про покер на сегодня: либо реальная актуальная новость/событие "
        "из мира покера, либо исторический факт, привязанный к этой дате. Если точной даты нет — "
        "дай познавательный покерный факт.\n"
        "3) Пожелание удачного дня и крупных заносов в турнирах.\n\n"
        "Требования:\n"
        "- Дружелюбный живой тон, без канцелярита.\n"
        "- 5–9 строк, компактно.\n"
        "- Эмодзи уместно, но без перебора.\n"
        "- Без markdown-разметки (* _ `), только обычный текст и эмодзи.\n"
        "- Не выдумывай конкретные суммы выигрышей, если не уверен в факте.\n"
        "- Не упоминай, что ты ИИ."
    )


def _openai_chat_sync(api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "temperature": 0.85,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты автор коротких утренних постов для дружеского покерного чата. "
                    "Пиши только на русском."
                ),
            },
            {"role": "user", "content": prompt},
        ],
    }
    req = urllib.request.Request(
        OPENAI_CHAT_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned no choices")
    message = choices[0].get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty content")
    return text


async def generate_morning_digest_text(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    when: Optional[datetime] = None,
) -> str:
    cfg = await load_morning_digest_config(conn, settings)
    if not cfg.api_key:
        raise RuntimeError("OpenAI API key is not configured")
    dt = when or _msk_now(cfg)
    prompt = _build_prompt(cfg, dt)
    return await asyncio.to_thread(_openai_chat_sync, cfg.api_key, cfg.model, prompt)


async def _already_sent_today(conn: aiosqlite.Connection, today: str) -> bool:
    last = await db.get_kv(conn, MORNING_DIGEST_KV_KEY, "")
    return str(last or "") == today


async def _mark_sent_today(conn: aiosqlite.Connection, today: str) -> None:
    await db.set_kv(conn, MORNING_DIGEST_KV_KEY, today)


def _in_send_window(cfg: MorningDigestConfig, now: datetime) -> bool:
    if now.hour < cfg.hour:
        return False
    if now.hour > cfg.hour:
        return False
    return True


async def send_morning_digest(
    bot: Bot,
    settings: Settings,
    conn: aiosqlite.Connection,
    db_lock: asyncio.Lock,
    *,
    force: bool = False,
    when: Optional[datetime] = None,
) -> bool:
    """Отправить утренний дайджест в чат статистики. Возвращает True, если отправлено."""
    async with db_lock:
        cfg = await load_morning_digest_config(conn, settings)
    if not cfg.enabled or not cfg.api_key:
        return False

    now = when or _msk_now(cfg)
    today = _today_key(cfg, now)
    if not force and not _in_send_window(cfg, now):
        return False

    async with db_lock:
        if not force and await _already_sent_today(conn, today):
            return False

    try:
        text = await generate_morning_digest_text(conn, settings, when=now)
    except Exception:
        log.exception("morning digest: OpenAI generation failed")
        return False

    chat_id = int(cfg.target_chat_id)
    try:
        await bot.send_message(chat_id, text)
    except TelegramForbiddenError:
        log.warning("morning digest: no permission to post in chat %s", chat_id)
        return False
    except TelegramBadRequest:
        log.warning("morning digest: bad request for chat %s", chat_id)
        return False
    except Exception:
        log.exception("morning digest: send failed for chat %s", chat_id)
        return False

    async with db_lock:
        await _mark_sent_today(conn, today)
    log.info("morning digest sent to chat %s for %s", chat_id, today)
    return True


async def run_morning_digest_scheduler(
    bot: Bot,
    settings: Settings,
    conn: aiosqlite.Connection,
    db_lock: asyncio.Lock,
) -> None:
    log.info("morning digest scheduler started")
    while True:
        try:
            async with db_lock:
                cfg = await load_morning_digest_config(conn, settings)
            if cfg.enabled and cfg.api_key:
                await send_morning_digest(bot, settings, conn, db_lock)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("morning digest scheduler tick failed")
        await asyncio.sleep(60)
