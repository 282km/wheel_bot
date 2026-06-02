from __future__ import annotations

from typing import Any

import aiosqlite
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.exceptions import TelegramBadRequest
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
        f"💰 Сумма заносов: {_fmt_money(float(data.get('deposits_sum', 0)))}",
        f"🏦 Сумма колес: {_fmt_money(float(data.get('prizes_sum', 0)))}",
        "",
        "💸 Топ-5 по сумме заносов:",
    ]
    for i, row in enumerate(data.get("top_depositors") or [], start=1):
        lines.append(f"{i} место")
        lines.append(f"👤 {row['nick']}")
        lines.append(f"💵 {_fmt_money(float(row['amount']))}")
        lines.append("")
    if not data.get("top_depositors"):
        lines.append("— нет данных")
        lines.append("")

    lines.extend(["", "🧾🍀 Топ-5 игрок выделил на колёса:"])
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


def setup_router(settings: Settings, conn: aiosqlite.Connection) -> Router:
    router = Router(name="wheel")

    from wheel_bot.posting import get_effective_stats_chat_id

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

    @router.message(Command("stat"))
    async def stats_cmd(message: Message) -> None:
        target_id = await get_effective_stats_chat_id(conn, settings)
        if message.chat.id != target_id:
            return
        await _stats_prompt(message)

    @router.message(F.text.lower() == "статистика")
    async def stats_text(message: Message) -> None:
        target_id = await get_effective_stats_chat_id(conn, settings)
        if message.chat.id != target_id:
            return
        await _stats_prompt(message)

    @router.callback_query(F.data.startswith("stats:"))
    async def stats_answer(cb: CallbackQuery) -> None:
        target_id = await get_effective_stats_chat_id(conn, settings)
        if not cb.message or cb.message.chat.id != target_id:
            await cb.answer()
            return
        key = cb.data.split(":", 1)[1]
        if key not in PERIOD_LABELS:
            await cb.answer("Неизвестный период", show_alert=True)
            return
        data = await stats_summary(conn, target_id, key)
        text = _format_stats_block(data)
        try:
            await cb.message.edit_text(text, reply_markup=_period_keyboard())
        except TelegramBadRequest:
            # Fallback for cases when Telegram refuses edit in channel/group context.
            await cb.message.answer(text, reply_markup=_period_keyboard())
        await cb.answer()

    return router
