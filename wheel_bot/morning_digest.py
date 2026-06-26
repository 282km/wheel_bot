from __future__ import annotations

import asyncio
import json
import logging
import secrets
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo

import aiosqlite
from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

from wheel_bot import db
from wheel_bot.morning_digest_settings import MorningDigestConfig, load_morning_digest_config
from wheel_bot.notify import notify_superadmins
from wheel_bot.poker_news_service import (
    _select_featured,
    attach_image,
    fetch_poker_news_digest,
    format_news_context,
    pick_featured_news,
)

if TYPE_CHECKING:
    from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.morning_digest")

MORNING_DIGEST_KV_KEY = "morning_digest_last_date"
OPENAI_CHAT_URL = "https://api.openai.com/v1/chat/completions"

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

_HISTORICAL_THEME_HINTS: tuple[str, ...] = (
    "легендарные руки и раздачи в истории покера",
    "знаменитые рекорды по выигрышам в турнирах",
    "история и зал славы покера (Poker Hall of Fame)",
    "великие хайроллеры и кэш-игроки прошлого",
    "интересные факты о WSOP за разные годы (кроме 2003)",
    "история European Poker Tour и его легендарные этапы",
    "знаменитые блефы и противостояния за столами",
    "эволюция онлайн-покера и культовые ники",
    "истории Фила Айви, Дойла Брансона, Стю Унгара и других легенд",
    "необычные и курьёзные случаи в турнирном покере",
    "женщины-чемпионки и их достижения в покере",
    "хайроллер-серии: Triton, Super High Roller Bowl и рекорды бай-инов",
)


@dataclass(frozen=True)
class MorningDigestPost:
    text: str
    image_url: Optional[str] = None
    source_mode: str = "historical"  # news | historical
    news_title: Optional[str] = None


def _msk_now(cfg: MorningDigestConfig) -> datetime:
    return datetime.now(ZoneInfo(cfg.timezone))


def _today_key(cfg: MorningDigestConfig, when: Optional[datetime] = None) -> str:
    dt = when or _msk_now(cfg)
    return dt.date().isoformat()


def _build_prompt(
    cfg: MorningDigestConfig,
    when: datetime,
    *,
    news_context: str,
    featured_title: Optional[str],
    use_news: bool,
    hot_topics: list[str],
) -> str:
    day = when.strftime("%d.%m.%Y")
    month_day = when.strftime("%d %B")
    if use_news and featured_title:
        topics_hint = ""
        if hot_topics:
            topics_hint = (
                f" Сейчас в ленте особенно заметны: {', '.join(hot_topics)} — "
                "но бери только то, что реально есть в списке новостей ниже."
            )
        fact_block = (
            "2) Главный блок — самая актуальная и яркая новость из списка ниже."
            f"{topics_hint} "
            f"Основная тема: «{featured_title}». Перескажи живо и понятно по-русски, без копипасты заголовка."
        )
    else:
        avoid_hint = secrets.choice(_HISTORICAL_THEME_HINTS)
        fact_block = (
            "2) Свежих ярких новостей в ленте нет — дай интересный исторический покерный факт. "
            f"Возьми тему из этой области: {avoid_hint}. "
            "Не рассказывай в очередной раз про Криса Манимейкера и WSOP 2003 — выбери что-то другое, "
            "каждый день разную тему."
        )

    greeting_hint = secrets.choice(_GREETING_AUDIENCE_HINTS)

    return (
        f"Сегодня {day} ({month_day}) по календарю.\n\n"
        "Напиши одно утреннее сообщение для покерного Telegram-чата на русском языке.\n\n"
        "Структура:\n"
        f"1) Короткое «доброе утро» с уместными эмодзи. Обращайся к чату в духе: "
        f"«{greeting_hint}» (можно слегка переформулировать, но в том же стиле).\n"
        f"{fact_block}\n"
        "3) Пожелание удачного дня и крупных заносов в турнирах.\n\n"
        "Требования:\n"
        "- Дружелюбный живой тон.\n"
        "- 6–10 строк, компактно.\n"
        "- Эмодзи уместно, для красоты, но без перебора.\n"
        "- Без markdown (* _ `), только текст и эмодзи.\n"
        "- Не используй обращение «покерные друзья» и близкие варианты («друзья покера» и т.п.).\n"
        "- Не выдумывай цифры и имена, которых нет в источниках ниже.\n"
        "- Не упоминай, что ты ИИ.\n\n"
        f"---\n{news_context}"
    )


def _openai_chat_sync(api_key: str, model: str, prompt: str) -> str:
    payload = {
        "model": model,
        "temperature": 0.8,
        "messages": [
            {
                "role": "system",
                "content": (
                    "Ты автор утренних постов для дружеского покерного Telegram-чата. "
                    "Пиши только на русском. Сам определяй актуальные темы по свежим новостям "
                    "(WSOP, EPT, WPT, APT, EAPT, Triton и другие серии — что сейчас в ленте). "
                    "Не зацикливайся на прошедших сериях, если их уже нет в свежих заголовках. "
                    "В приветствии обращайся к чату по-братски (братва, покерная братва, братва по масти и т.п.), "
                    "никогда не пиши «покерные друзья»."
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
    with urllib.request.urlopen(req, timeout=45) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("OpenAI returned no choices")
    message = choices[0].get("message") or {}
    text = str(message.get("content") or "").strip()
    if not text:
        raise RuntimeError("OpenAI returned empty content")
    return text


async def prepare_morning_digest_post(
    conn: aiosqlite.Connection,
    settings: Settings,
    *,
    when: Optional[datetime] = None,
) -> MorningDigestPost:
    cfg = await load_morning_digest_config(conn, settings)
    if not cfg.api_key:
        raise RuntimeError("OpenAI API key is not configured")

    dt = when or _msk_now(cfg)
    news_warnings: list[str] = []
    news_items: list = []
    hot_topics: list[str] = []
    featured = None
    try:
        digest = await fetch_poker_news_digest(limit=12)
        news_items = digest.items
        hot_topics = digest.hot_topics
        featured = _select_featured(news_items)
        if featured is not None:
            featured = await attach_image(featured)
    except Exception as exc:
        news_warnings.append(f"не удалось загрузить RSS: {exc}")
        log.exception("morning digest: poker news fetch failed")

    use_news = featured is not None
    news_context = format_news_context(news_items, hot_topics)
    if news_warnings:
        news_context = f"{news_context}\n\n(Предупреждение: {'; '.join(news_warnings)})"

    prompt = _build_prompt(
        cfg,
        dt,
        news_context=news_context,
        featured_title=featured.title if featured else None,
        use_news=use_news,
        hot_topics=hot_topics,
    )
    text = await asyncio.to_thread(_openai_chat_sync, cfg.api_key, cfg.model, prompt)

    if use_news and featured:
        return MorningDigestPost(
            text=text,
            image_url=featured.image_url,
            source_mode="news",
            news_title=featured.title,
        )
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
    image_warning: Optional[str] = None
    if post.image_url:
        try:
            await bot.send_photo(chat_id, photo=post.image_url, caption=post.text)
            return True, None
        except (TelegramBadRequest, TelegramForbiddenError) as exc:
            image_warning = f"фото не отправилось ({exc}), отправлен только текст"
            log.warning("morning digest: photo send failed, fallback to text: %s", exc)
        except Exception as exc:
            image_warning = f"фото не отправилось ({exc}), отправлен только текст"
            log.exception("morning digest: photo send failed")

    await bot.send_message(chat_id, post.text)
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
            if cfg.enabled and cfg.api_key:
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
