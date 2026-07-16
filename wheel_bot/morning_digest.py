from __future__ import annotations

import asyncio
import logging
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from wheel_bot.timezones import get_timezone

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from wheel_bot import db
from wheel_bot.morning_digest_settings import MorningDigestConfig, load_morning_digest_config
from wheel_bot.notify import notify_superadmins
from wheel_bot.poker_news_service import (
    PokerNewsItem,
    _select_featured,
    attach_image,
    fetch_poker_news_digest,
)

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.morning_digest")

MORNING_DIGEST_KV_KEY = "morning_digest_last_date"
_TELEGRAM_CAPTION_MAX = 1020

# Обращения в приветствии — каждый день случайный стиль (не «покерные друзья»).
_GREETING_AUDIENCE_HINTS: tuple[str, ...] = (
    "покерная братва",
    "братва",
    "братва по масти",
    "братва за столом",
    "фетровые бойцы",
    "охотники за банками",
    "гриндеры",
    "любители зелёного сукна",
    "карточные акулы",
    "короли ривера",
    "масти в сборе",
    "бойцы турнирного поля",
    "кэшовые хищники",
    "братва по флешу",
    "клуб любителей бордов",
    "стражи большого блайнда",
    "ночные волки покерных румов",
    "братва с тузом в рукаве",
    "команда по заносам",
    "фанаты красивого ривера",
)

_HISTORICAL_FACTS: tuple[str, ...] = (
    "В 1970 году первый WSOP собрал всего 7 игроков — победителя выбрали голосованием.",
    "Фил Айви выиграл 10 браслетов WSOP и считается одним из сильнейших игроков всех времён.",
    "Дойл Брансон написал «Super/System» — книгу, которая изменила понимание покера у целого поколения.",
    "Стю Унгар выиграл Main Event WSOP три раза — рекорд, который долгое время казался недостижимым.",
    "Ванесса Selbst — одна из самых успешных турнирных игроков среди женщин в истории покера.",
    "Серия Triton Super High Roller прославилась турнирами с бай-инами в сотни тысяч долларов.",
    "EPT Barcelona традиционно собирает одни из самых больших полей в Европе.",
    "«Мёртвая рука» — тузы и восьмёрки — связана с легендой об убийстве Wild Bill Hickok за покерным столом.",
    "Антонио Эсфандиари выиграл Big One for One Drop и $18,3 млн — один из крупнейших призов в истории.",
    "Daniel Negreanu держит рекорд WSOP по количеству кэшей на серии.",
    "Онлайн-бум 2003–2006 годов резко увеличил число игроков по всему миру.",
    "Pot-Limit Omaha за последние годы стал одним из самых популярных форматов в кэше и турнирах.",
    "Первые телевизионные трансляции WSOP начались в 1973 году — покер вышел к широкой аудитории.",
    "Irish Open — один из старейших и самых узнаваемых живых турниров в Европе.",
    "В покере нет «счёта карт» как в блэкджеке — каждая раздача независима, зато важна математика и психология.",
    "Супер High Roller Bowl и Poker Masters сделали хайроллер-формат зрелищем для фанатов.",
    "Женский турнир WSOP Ladies Event проходит с 1977 года и остаётся традицией серии.",
    "Кэш-игры с блайндами $100/$200 и выше — отдельная вселенная, где сидят легенды вроде Фила Айви и Тома Двана.",
)


@dataclass(frozen=True)
class MorningDigestPost:
    text: str
    image_url: Optional[str] = None
    source_mode: str = "historical"  # news | historical
    news_title: Optional[str] = None
    news_link: Optional[str] = None


def _msk_now(cfg: MorningDigestConfig) -> datetime:
    return datetime.now(get_timezone(cfg.timezone))


def _today_key(cfg: MorningDigestConfig, when: Optional[datetime] = None) -> str:
    dt = when or _msk_now(cfg)
    return dt.date().isoformat()


def _truncate_text(text: str, max_len: int) -> str:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(text) <= max_len:
        return text
    cut = text[: max_len - 1].rsplit(" ", 1)[0]
    return f"{cut}…"


def _trim_for_telegram(text: str, *, max_len: int = _TELEGRAM_CAPTION_MAX) -> str:
    text = str(text or "").strip()
    if len(text) <= max_len:
        return text
    trimmed = text[: max_len - 1].rsplit("\n", 1)[0].rstrip()
    if len(trimmed) > max_len - 2:
        trimmed = trimmed[: max_len - 2].rsplit("\n", 1)[0].rstrip()
    return f"{trimmed}…"


def _format_news_post(
    when: datetime,
    featured: PokerNewsItem,
    *,
    hot_topics: list[str],
) -> str:
    greeting = secrets.choice(_GREETING_AUDIENCE_HINTS)
    lines = [
        f"☀️ Доброе утро, {greeting}!",
        "",
        f"📰 По данным {featured.source}:",
        featured.title.strip(),
    ]
    if hot_topics:
        lines.extend(["", f"🔥 В ленте сейчас заметны: {', '.join(hot_topics)}."])
    summary = _truncate_text(re.sub(r"<[^>]+>", " ", featured.summary), 320)
    if summary:
        lines.extend(["", summary])
    if featured.link:
        lines.extend(["", f"🔗 {featured.link}"])
    lines.extend(["", "🍀 Удачного дня и крупных заносов за столами!"])
    return _trim_for_telegram("\n".join(lines))


def _format_historical_post(when: datetime) -> str:
    greeting = secrets.choice(_GREETING_AUDIENCE_HINTS)
    fact = secrets.choice(_HISTORICAL_FACTS)
    lines = [
        f"☀️ Доброе утро, {greeting}!",
        "",
        "📚 Покерный факт дня:",
        fact,
        "",
        "🍀 Удачного дня и крупных заносов за столами!",
    ]
    return "\n".join(lines)


async def prepare_morning_digest_post(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    when: Optional[datetime] = None,
) -> MorningDigestPost:
    cfg = await load_morning_digest_config(conn, settings)
    dt = when or _msk_now(cfg)
    featured = None
    hot_topics: list[str] = []
    try:
        digest = await fetch_poker_news_digest(limit=12)
        hot_topics = digest.hot_topics
        featured = _select_featured(digest.items)
        if featured is not None:
            featured = await attach_image(featured)
    except Exception:
        log.exception("morning digest: poker news fetch failed")

    if featured is not None:
        text = _format_news_post(dt, featured, hot_topics=hot_topics)
        return MorningDigestPost(
            text=text,
            image_url=featured.image_url,
            source_mode="news",
            news_title=featured.title,
            news_link=featured.link,
        )

    text = _format_historical_post(dt)
    return MorningDigestPost(text=text, source_mode="historical")


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
    """Отправка поста как в чат: фото с подписью или текст. Возвращает (успех, предупреждение о фото)."""
    text = (post.text or "").strip()
    if not text:
        raise ValueError("morning digest post text is empty")

    image_warning: Optional[str] = None
    photo = (post.image_url or "").strip()
    if photo.startswith("//"):
        photo = "https:" + photo
    if photo.startswith(("http://", "https://")):
        try:
            await bot.send_photo(chat_id, photo=photo, caption=text)
            return True, None
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            image_warning = f"фото не отправилось ({exc}), отправлен только текст"
            log.warning("morning digest: photo send failed, fallback to text: %s", exc)
        except Exception as exc:
            image_warning = f"фото не отправилось ({exc}), отправлен только текст"
            log.exception("morning digest: photo send failed")

    await bot.send_message(chat_id, text)
    return True, image_warning


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
        "morning digest sent to chat %s for %s (mode=%s, title=%r)",
        chat_id,
        today,
        post.source_mode,
        post.news_title,
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
