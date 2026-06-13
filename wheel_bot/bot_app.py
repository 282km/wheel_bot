from __future__ import annotations

import asyncio
import logging
import re
import secrets
from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    WebAppInfo,
)

from wheel_bot import db
from wheel_bot.config import Settings
from wheel_bot.stats_service import stats_summary


PERIOD_LABELS: dict[str, str] = {
    "all": "Вся история",
    "prev_year": "Прошлый год",
    "cur_year": "Текущий год",
    "prev_month": "Прошлый месяц",
    "cur_month": "Текущий месяц",
}

# Ответы в чате статистики на «ник писать?» и похожие вопросы.
NICK_WRITE_WAIT_REPLIES: tuple[str, ...] = (
    "Ещё не вечер.",
    "Пока рано.",
    "Подожди немного.",
    "Не спеши — колесо само не убежит.",
    "Рано ещё ник подавать.",
    "Потерпи чуть-чуть.",
    "Сейчас не время.",
    "Не торопись — объявят.",
    "Погоди, скоро скажут.",
    "Ещё рановато.",
    "Не сейчас.",
    "Подожди — всё в своё время.",
    "Терпение — ключ к колесу.",
    "Рано писать ник.",
    "Объявят, когда можно будет.",
    "Пока молчи и жди сигнала.",
    "Ещё не час ников.",
    "Чуть позже.",
    "Не торопи события.",
    "Посиди пока — рано.",
    "Колесо крутится не по расписанию ников.",
    "Пока нет — дождись объявления.",
    "Рано, как утренний рейз без карт.",
    "Ник подождёт — ты тоже.",
)


def _period_keyboard() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text=PERIOD_LABELS["all"], callback_data="stats:all"),
            InlineKeyboardButton(text=PERIOD_LABELS["prev_year"], callback_data="stats:prev_year"),
            InlineKeyboardButton(text=PERIOD_LABELS["cur_year"], callback_data="stats:cur_year"),
        ],
        [
            InlineKeyboardButton(text=PERIOD_LABELS["prev_month"], callback_data="stats:prev_month"),
            InlineKeyboardButton(text=PERIOD_LABELS["cur_month"], callback_data="stats:cur_month"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _fmt_money(x: float) -> str:
    s = f"{x:g}"
    return f"${s.replace('.', ',')}"


def _format_stats_block(data: dict[str, Any]) -> str:
    period_key = str(data.get("period"))
    title = PERIOD_LABELS.get(period_key, period_key)
    lines = [
        f"📊 Статистика колеса — {title}",
        "",
        f"🎡 Колес за период: {data.get('wheels_count', 0)}",
        f"🏦 Сумма колес: {_fmt_money(float(data.get('prizes_sum', 0)))}",
        "",
        "🧾🍀 Топ-5 игрок выделил на колёса:",
    ]
    for i, row in enumerate(data.get("top_allocated") or [], start=1):
        lines.append(f"{i} место")
        lines.append(f"👤 {row['nick']}")
        lines.append(f"💸 {_fmt_money(float(row['amount']))}")
        lines.append("")
    if not data.get("top_allocated"):
        lines.append("— нет данных")
        lines.append("")

    lines.extend(["", "🏆 Топ-5 по сумме выигрышей в колесах:"])
    for i, row in enumerate(data.get("top_win_amounts") or [], start=1):
        lines.append(f"{i} место")
        lines.append(f"👤 {row['nick']}")
        lines.append(f"💵 {_fmt_money(float(row['amount']))}")
        lines.append("")
    if not data.get("top_win_amounts"):
        lines.append("— нет данных")
        lines.append("")

    lines.extend(["", "🥇 Топ-5 по количеству побед в колесах:"])
    for i, row in enumerate(data.get("top_win_counts") or [], start=1):
        lines.append(f"{i} место")
        lines.append(f"👤 {row['nick']}")
        lines.append(f"✅ Побед: {int(row['wins'])}")
        lines.append("")
    if not data.get("top_win_counts"):
        lines.append("— нет данных")

    text = "\n".join(lines).strip()
    if len(text) <= 3900:
        return text
    return text[:3899] + "…"


def _first_command_token(text: str) -> str:
    return text.strip().split(maxsplit=1)[0].split("@")[0].lower()


def _is_stat_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/stat"


def _is_chatid_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/chatid"


def _is_nick_write_question(message: Message) -> bool:
    """Вопрос вроде «ник писать?», «пишу?» в чате колеса."""
    if not message.text:
        return False
    raw = message.text.strip()
    if not raw or raw.startswith("/"):
        return False
    t = raw.lower()
    if "статистик" in t:
        return False
    if re.fullmatch(r"пишу\s*\?", raw, flags=re.IGNORECASE):
        return True
    if "ник" not in t:
        return False
    if re.search(r"ник\s+писат", t) or re.search(r"писат\w*\s+ник", t):
        return True
    if re.search(r"можно\s+.*ник", t) and "писат" in t:
        return True
    if re.search(r"ник\s+уже", t) or re.search(r"уже\s+.*ник", t):
        return True
    if re.search(r"когда\s+.*ник", t) and ("писат" in t or "?" in raw):
        return True
    if re.search(r"ник\s*\?", t) and any(w in t for w in ("можно", "уже", "когда", "писат", "пиш")):
        return True
    return False


def _admin_webapp_keyboard(settings: Settings) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎡 Управление колесом",
                    web_app=WebAppInfo(url=settings.webapp_url),
                )
            ]
        ]
    )


def setup_router(settings: Settings, conn: aiosqlite.Connection, db_lock: asyncio.Lock) -> Router:
    router = Router(name="wheel")
    log = logging.getLogger("wheel_bot.bot")

    from wheel_bot.destinations import read_destination_config
    from wheel_bot.posting import get_stats_chat_id

    def _configured_stats_chat_id() -> int:
        return get_stats_chat_id(settings)

    async def _stats_chat_mismatch_reply(message: Message, target_id: int) -> bool:
        chat_id = int(message.chat.id)
        if chat_id == int(target_id):
            return False
        log.warning("/stat: chat_id=%s configured_stats_chat=%s", chat_id, target_id)
        hint = (
            "⚠️ Статистика для этого бота привязана к другому чату.\n\n"
            f"ID этого чата: `{chat_id}`\n"
            f"В настройках бота: `{target_id}`\n\n"
            "Задайте `TARGET_CHAT_ID` в `.env` на сервере (ID этого чата), "
            "затем `sudo systemctl restart wheel-bot`.\n\n"
            "Для проверки ID админ может написать здесь: /chatid"
        )
        try:
            await message.answer(hint, parse_mode="Markdown")
        except TelegramBadRequest:
            await message.answer(
                hint.replace("`", ""),
            )
        return True

    @router.message(Command("start"))
    async def cmd_start(message: Message) -> None:
        if message.chat.type != "private":
            return
        tg_id = message.from_user.id if message.from_user else message.chat.id
        role = await db.ensure_user(conn, tg_id)
        if role in ("admin", "superadmin"):
            await message.answer(
                "Вы администратор колеса.\n"
                "Откройте приложение кнопкой ниже (не по ссылке в браузере).\n"
                "Статистика — в общем чате: /stat или «Статистика».",
                reply_markup=_admin_webapp_keyboard(settings),
            )
            return
        await message.answer("В общем чате доступна команда статистики «/stat» или текст «Статистика».")

    @router.message(Command("app", "webapp"))
    async def cmd_app(message: Message) -> None:
        await cmd_wheel(message)

    @router.message(Command("колесо"))
    async def cmd_wheel(message: Message) -> None:
        if message.chat.type != "private":
            return
        tg_id = message.from_user.id if message.from_user else message.chat.id
        role = await db.ensure_user(conn, tg_id)
        if role in ("admin", "superadmin"):
            await message.answer(
                "Нажмите кнопку, чтобы открыть WebApp внутри Telegram:",
                reply_markup=_admin_webapp_keyboard(settings),
            )
            return
        await message.answer("У вас нет прав администратора для управления колесом.")

    @router.message(F.chat.type == "private", F.text.lower() == "управление колесом")
    async def wheel_text(message: Message) -> None:
        await cmd_wheel(message)

    async def _stats_prompt(message: Message) -> None:
        await message.answer("Выберите период для статистики:", reply_markup=_period_keyboard())

    @router.message(lambda message: _is_chatid_command(message))
    async def cmd_chatid(message: Message) -> None:
        """Помощник настройки: показать id чата и сверку с конфигом (только админы)."""
        try:
            if message.chat.type == "private":
                uid = message.from_user.id if message.from_user else message.chat.id
                await message.answer(f"Ваш Telegram user id: {uid}")
                return
            tg_id = message.from_user.id if message.from_user else 0
            async with db_lock:
                role = await db.ensure_user(conn, tg_id)
            if role not in ("admin", "superadmin"):
                await message.answer("Команда /chatid только для админов бота.")
                return
            async with db_lock:
                cfg = await read_destination_config(conn, settings)
            target_id = int(cfg["stats_chat_id"])
            chat_id = int(message.chat.id)
            channel_id = cfg["channel_chat_id"]
            post_chat_id = cfg["post_chat_id"]
            match = "✅ совпадает — /stat здесь" if chat_id == target_id else "❌ не совпадает — /stat здесь не сработает"
            ch_line = f"`{channel_id}`" if channel_id is not None else "не задан"
            text = (
                f"ID этого чата: `{chat_id}`\n"
                f"Тип: {message.chat.type}\n\n"
                f"Чат для /stat и БД: `{target_id}`\n"
                f"Канал для постов колеса: {ch_line}\n"
                f"Сейчас посты уходят в: `{post_chat_id}` (режим {cfg['post_target']})\n\n"
                f"{match}\n\n"
                f"ID задаются только в .env: TARGET_CHAT_ID и WHEEL_CHANNEL_ID"
            )
            try:
                await message.answer(text, parse_mode="Markdown")
            except TelegramBadRequest:
                await message.answer(text.replace("`", ""))
        except TelegramForbiddenError:
            log.warning("chatid: no send permission in chat %s", message.chat.id)
        except Exception:
            log.exception("chatid handler failed")

    async def _handle_stat(message: Message) -> None:
        try:
            if message.chat.type == "channel":
                await message.answer(
                    "Команда /stat работает в группе (супергруппе), не в канале. "
                    "Вызовите её в чате, где собирается статистика."
                )
                return
            if message.chat.type == "private":
                target_id = _configured_stats_chat_id()
                await message.answer(
                    "Статистика доступна в общем чате команды, не в личке.\n\n"
                    f"Напишите /stat в чате с ID {target_id}.\n\n"
                    "Если в BotFather включён Group Privacy — используйте "
                    "/stat@имя_бота или отключите Privacy Mode."
                )
                return
            target_id = _configured_stats_chat_id()
            chat_id = int(message.chat.id)
            log.info("stat: chat_id=%s target_id=%s type=%s text=%r", chat_id, target_id, message.chat.type, message.text)
            if await _stats_chat_mismatch_reply(message, target_id):
                return
            await _stats_prompt(message)
        except TelegramForbiddenError:
            log.warning("stat: bot cannot send messages in chat %s", message.chat.id)
        except Exception:
            log.exception("stat handler failed for chat %s", message.chat.id)
            try:
                await message.answer("Ошибка при открытии статистики. Проверьте логи wheel-bot на сервере.")
            except Exception:
                pass

    @router.message(lambda message: _is_stat_command(message))
    async def stats_cmd(message: Message) -> None:
        await _handle_stat(message)

    @router.message(lambda m: bool(m.text and m.text.strip().lower() == "статистика"))
    async def stats_text(message: Message) -> None:
        await _handle_stat(message)

    @router.message(lambda message: _is_nick_write_question(message))
    async def nick_write_wait(message: Message) -> None:
        """В чате /stat на «ник писать?» — шутливый отказ подождать."""
        try:
            if message.chat.type not in ("group", "supergroup"):
                return
            chat_id = int(message.chat.id)
            target_id = _configured_stats_chat_id()
            if chat_id != int(target_id):
                return
            reply = secrets.choice(NICK_WRITE_WAIT_REPLIES)
            log.info("nick_write_wait: chat_id=%s text=%r -> %r", chat_id, message.text, reply)
            await message.reply(reply)
        except TelegramForbiddenError:
            log.warning("nick_write_wait: no send permission in chat %s", message.chat.id)
        except Exception:
            log.exception("nick_write_wait failed for chat %s", message.chat.id)

    @router.callback_query(F.data.startswith("stats:"))
    async def stats_answer(cb: CallbackQuery) -> None:
        target_id = _configured_stats_chat_id()
        if not cb.message or int(cb.message.chat.id) != int(target_id):
            await cb.answer()
            return
        key = cb.data.split(":", 1)[1]
        if key not in PERIOD_LABELS:
            await cb.answer("Неизвестный период", show_alert=True)
            return
        try:
            async with db_lock:
                data = await stats_summary(conn, target_id, key)
            text = _format_stats_block(data)
            try:
                await cb.message.edit_text(text, reply_markup=_period_keyboard())
            except TelegramBadRequest:
                await cb.message.answer(text, reply_markup=_period_keyboard())
            await cb.answer()
        except Exception:
            log.exception("stats callback failed")
            await cb.answer("Ошибка статистики", show_alert=True)

    return router
