from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from wheel_bot import db
from wheel_bot.game_service import format_morning_games_digest
from wheel_bot.morning_digest_settings import MorningDigestConfig, load_morning_digest_config
from wheel_bot.notify import notify_superadmins

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.morning_digest")

MORNING_DIGEST_KV_KEY = "morning_digest_last_date"


@dataclass(frozen=True)
class MorningDigestPost:
    text: str
    image_url: Optional[str] = None
    source_mode: str = "games"
    news_title: Optional[str] = None
    news_link: Optional[str] = None


def _msk_now(cfg: MorningDigestConfig) -> datetime:
    from wheel_bot.timezones import get_timezone

    return datetime.now(get_timezone(cfg.timezone))


def _today_key(cfg: MorningDigestConfig, when: Optional[datetime] = None) -> str:
    dt = when or _msk_now(cfg)
    return dt.date().isoformat()


async def prepare_morning_digest_post(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    when: Optional[datetime] = None,
) -> MorningDigestPost:
    cfg = await load_morning_digest_config(conn, settings)
    dt = when or _msk_now(cfg)
    text = await format_morning_games_digest(conn, when=dt)
    return MorningDigestPost(text=text, source_mode="games")


async def generate_morning_digest_text(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    when: Optional[datetime] = None,
) -> str:
    post = await prepare_morning_digest_post(conn, settings, when=when)
    return post.text


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


async def _notify_digest_error(
    bot: Bot,
    settings: Settings,
    title: str,
    details: str,
) -> None:
    text = (
        f"⚠️ *Утренний дайджест — ошибка*\n\n"
        f"*{title}*\n\n"
        f"{details}\n\n"
        f"Проверьте вкладку «Админ» → «Утренний дайджест» или логи `wheel-bot`."
    )
    await notify_superadmins(bot, settings, text, log=log, parse_mode="Markdown")


async def send_morning_post_to_chat(
    bot: Bot,
    chat_id: int,
    post: MorningDigestPost,
) -> tuple[bool, Optional[str]]:
    text = (post.text or "").strip()
    if not text:
        raise ValueError("morning digest post text is empty")
    await bot.send_message(chat_id, text)
    return True, None


async def send_morning_digest(
    bot: Bot,
    settings: Settings,
    conn: aiosqlite.Connection,
    db_lock: asyncio.Lock,
    *,
    force: bool = False,
    when: Optional[datetime] = None,
) -> bool:
    """Отправить утреннюю сводку блэкджека в чат статистики."""
    async with db_lock:
        cfg = await load_morning_digest_config(conn, settings)
    if not cfg.enabled:
        return False

    now = when or _msk_now(cfg)
    today = _today_key(cfg, now)
    if not force and not _in_send_window(cfg, now):
        return False

    async with db_lock:
        if not force and await _already_sent_today(conn, today):
            return False

    try:
        async with db_lock:
            post = await prepare_morning_digest_post(conn, settings, when=now)
    except Exception as exc:
        log.exception("morning digest: generation failed")
        await _notify_digest_error(
            bot,
            settings,
            "Не удалось сгенерировать пост",
            str(exc),
        )
        return False

    chat_id = int(cfg.target_chat_id)
    try:
        ok, image_warning = await send_morning_post_to_chat(bot, chat_id, post)
        if not ok:
            return False
        if image_warning:
            await notify_superadmins(
                bot,
                settings,
                f"ℹ️ Утренний дайджест отправлен в чат, но:\n{image_warning}",
                log=log,
            )
    except TelegramForbiddenError as exc:
        log.warning("morning digest: no permission to post in chat %s", chat_id)
        await _notify_digest_error(bot, settings, "Нет прав писать в чат статистики", str(exc))
        return False
    except TelegramBadRequest as exc:
        log.warning("morning digest: bad request for chat %s", chat_id)
        await _notify_digest_error(bot, settings, "Telegram отклонил сообщение", str(exc))
        return False
    except Exception as exc:
        log.exception("morning digest: send failed for chat %s", chat_id)
        await _notify_digest_error(bot, settings, "Ошибка отправки в чат", str(exc))
        return False

    async with db_lock:
        await _mark_sent_today(conn, today)
    log.info(
        "morning digest sent to chat %s for %s (mode=%s)",
        chat_id,
        today,
        post.source_mode,
    )
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
            if cfg.enabled:
                await send_morning_digest(bot, settings, conn, db_lock)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("morning digest scheduler tick failed")
            await _notify_digest_error(
                bot,
                settings,
                "Сбой планировщика утреннего дайджеста",
                str(exc),
            )
        await asyncio.sleep(60)
