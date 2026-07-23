from __future__ import annotations

import asyncio
import logging
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
from wheel_bot.bonus_messages import format_bonus_admin_notify, format_bonus_result
from wheel_bot.bonus_service import try_daily_bonus
from wheel_bot.config import Settings
from wheel_bot.game_messages import (
    format_bowling_result,
    format_duel_result,
    format_games_welcome,
    format_leaderboard,
    format_slots_result,
    format_user_stats,
)
from wheel_bot.game_service import (
    check_cooldown,
    format_wait,
    get_user_rank,
    record_play,
    score_bowling,
    score_slots,
    user_week_stats,
    weekly_summary,
)
from wheel_bot.stats_service import losers_summary, participant_wheel_wins, stats_summary


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

    top_bonus = data.get("top_bonus_winners") or []
    if top_bonus:
        lines.extend(["", "🍀🎁 Бонусы /bonus — кто поймал удачу:"])
        for i, row in enumerate(top_bonus, start=1):
            wins = int(row["wins"])
            lines.append(f"{i} место")
            lines.append(f"👤 {row['label']}")
            lines.append(f"🎯 Выигрышей: {wins}")
            lines.append(f"💰 На сумму: {_fmt_money(float(row['total']))}")
            lines.append("")

    text = "\n".join(lines).strip()
    if len(text) <= 3900:
        return text
    return text[:3899] + "…"


def _format_luz_block(data: dict[str, Any]) -> str:
    total_wheels = int(data.get("total_wheels", 0))
    prizes_sum = float(data.get("prizes_sum", 0))
    lines = [
        "📉 Статистика лузеров — вся история",
        "",
        f"🎡 Всего колёс: {total_wheels}",
        f"🏦 Разыграно всего: {_fmt_money(prizes_sum)}",
        "",
        "😢 Топ-10 по малому числу побед:",
    ]
    worst_wins = data.get("worst_wins") or []
    if not worst_wins:
        lines.append("— нет активных участников")
    else:
        for i, row in enumerate(worst_wins, start=1):
            wins = int(row["wins"])
            lines.append(f"{i} место")
            lines.append(f"👤 {row['nick']}")
            lines.append(f"📊 Побед: {wins} из {total_wheels}")
            lines.append("")

    lines.extend(["", "💸 Топ-10 по малой сумме выигрышей:"])
    worst_money = data.get("worst_money") or []
    if not worst_money:
        lines.append("— нет активных участников")
    else:
        for i, row in enumerate(worst_money, start=1):
            lines.append(f"{i} место")
            lines.append(f"👤 {row['nick']}")
            lines.append(f"💵 Выиграл: {_fmt_money(float(row['amount']))} из {_fmt_money(prizes_sum)}")
            lines.append("")

    text = "\n".join(lines).strip()
    if len(text) <= 3900:
        return text
    return text[:3899] + "…"


def _format_winreport_block(data: dict[str, Any]) -> str:
    lines = [
        f"🎡 {data['label']}",
        "",
        f"📊 Побед в колёсах за всю историю: {int(data['wins'])} из {int(data['total_wheels'])}",
        f"💵 Выиграно: {_fmt_money(float(data['won_sum']))}",
    ]
    if data.get("is_hidden"):
        lines.append("")
        lines.append("⚠️ Участник сейчас скрыт в списке.")
    return "\n".join(lines)


def _first_command_token(text: str) -> str:
    return text.strip().split(maxsplit=1)[0].split("@")[0].lower()


def _is_stat_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/stat"


def _is_luz_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/luz"


def _winreport_nick(message: Message) -> str | None:
    if not message.text:
        return None
    token = _first_command_token(message.text)
    if token != "/winreport":
        return None
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        return None
    return parts[1].strip()


def _is_chatid_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/chatid"


def _is_bonus_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/bonus"


def _is_games_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/games"


def _is_slots_command(message: Message) -> bool:
    if not message.text:
        return False
    token = _first_command_token(message.text)
    return token in ("/slots", "/play")


def _is_bowling_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/bowling"


def _is_duel_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/duel"


def _games_subcommand(message: Message) -> str | None:
    if not message.text:
        return None
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip().lower()


def _chat_user_label(user) -> str:
    if not user:
        return "Игрок"
    if user.username:
        return f"@{user.username}"
    name = (user.first_name or "").strip()
    return name or "Игрок"


def _bonus_user_label(message: Message) -> str:
    user = message.from_user
    return _chat_user_label(user)


async def _notify_superadmins_bonus_win(
    message: Message,
    settings: Settings,
    user_label: str,
    amount: float,
    log: logging.Logger,
) -> None:
    user = message.from_user
    if not user or not settings.superadmin_ids:
        return
    text = format_bonus_admin_notify(user_label, int(user.id), amount)
    bot = message.bot
    for admin_id in settings.superadmin_ids:
        try:
            await bot.send_message(int(admin_id), text, parse_mode="Markdown")
        except TelegramBadRequest:
            try:
                await bot.send_message(int(admin_id), text.replace("*", "").replace("`", ""))
            except Exception:
                log.exception("bonus admin notify: bad request fallback failed for %s", admin_id)
        except TelegramForbiddenError:
            log.warning(
                "bonus admin notify: cannot DM superadmin %s — напишите боту /start в личке",
                admin_id,
            )
        except Exception:
            log.exception("bonus admin notify failed for superadmin %s", admin_id)


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

    async def _stats_chat_mismatch_reply(message: Message, target_id: int, command: str = "/stat") -> bool:
        chat_id = int(message.chat.id)
        if chat_id == int(target_id):
            return False
        log.warning("%s: chat_id=%s configured_stats_chat=%s", command, chat_id, target_id)
        hint = (
            f"⚠️ Команда {command} для этого бота привязана к другому чату.\n\n"
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

    @router.message(Command("morning_test"))
    async def morning_test_cmd(message: Message) -> None:
        """Реальный тест дайджеста в личку superadmin: как уйдёт в чат (с фото если есть)."""
        if message.chat.type != "private":
            return
        tg_id = message.from_user.id if message.from_user else 0
        async with db_lock:
            role = await db.ensure_user(conn, tg_id)
        if role != "superadmin":
            await message.answer("Команда только для superadmin.")
            return
        await message.answer("Генерирую утренний пост… пришлю через ~5–20 сек.")

        async def _run_test() -> None:
            try:
                from wheel_bot.morning_digest import (
                    prepare_morning_digest_post,
                    send_morning_post_to_chat,
                )

                async with db_lock:
                    post = await prepare_morning_digest_post(conn, settings)
                ok, image_warning = await send_morning_post_to_chat(
                    message.bot,
                    int(message.chat.id),
                    post,
                )
                if not ok:
                    await message.answer("Не удалось отправить тест. Проверьте логи wheel-bot.")
                    return
                mode = "сводка мини-игр" if post.source_mode == "games" else post.source_mode
                lines = [
                    "🧪 Тест отправлен вам (в чат статистики не ушло).",
                    f"Режим: {mode}",
                ]
                if post.news_title:
                    lines.append(f"Тема: {post.news_title}")
                if post.news_link:
                    lines.append(f"Источник: {post.news_link}")
                lines.append("📷 С фото" if post.image_url else "📷 Без фото")
                if post.image_url:
                    lines.append(f"Фото: {post.image_url}")
                await message.answer("\n".join(lines))
                if image_warning:
                    await message.answer(f"⚠️ {image_warning}")
            except Exception as exc:
                log.exception("morning_test failed")
                try:
                    detail = str(exc).strip() or exc.__class__.__name__
                    await message.answer(f"Ошибка генерации: {detail[:400]}")
                except Exception:
                    pass

        asyncio.create_task(_run_test())

    @router.message(Command("morning_send"))
    async def morning_send_cmd(message: Message) -> None:
        """Принудительно отправить утренний пост в чат статистики (superadmin)."""
        if message.chat.type != "private":
            return
        tg_id = message.from_user.id if message.from_user else 0
        async with db_lock:
            role = await db.ensure_user(conn, tg_id)
        if role != "superadmin":
            await message.answer("Команда только для superadmin.")
            return
        from wheel_bot.morning_digest_settings import load_morning_digest_config

        async with db_lock:
            cfg = await load_morning_digest_config(conn, settings)
        if not cfg.enabled:
            await message.answer("Утренний дайджест выключен. Включите во вкладке «Админ» в WebApp.")
            return
        await message.answer("Готовлю пост и отправляю в чат… ~5–20 сек.")

        async def _run_send() -> None:
            try:
                from wheel_bot.morning_digest import send_morning_digest

                ok = await send_morning_digest(
                    message.bot,
                    settings,
                    conn,
                    db_lock,
                    force=True,
                )
                if ok:
                    await message.answer(f"Утренний пост отправлен в чат {settings.target_chat_id}.")
                else:
                    await message.answer("Не удалось отправить пост. Проверьте логи wheel-bot.")
            except Exception as exc:
                log.exception("morning_send failed")
                try:
                    await message.answer(f"Ошибка: {exc}")
                except Exception:
                    pass

        asyncio.create_task(_run_send())

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
                "Статистика — в общем чате: /stat или «Статистика».\n"
                "Лузеры — /luz.\n"
                "Бонус удачи — /bonus (раз в сутки в чате статистики).\n"
                "Мини-игры — /games help (слоты, боулинг, дуэли).",
                reply_markup=_admin_webapp_keyboard(settings),
            )
            return
        await message.answer(
            "В общем чате: /stat или «Статистика», /luz — статистика лузеров, "
            "/bonus — попытка удачи раз в сутки.\n"
            "Мини-игры: /games help"
        )

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
            match = "✅ совпадает — /stat, /luz, /bonus и /games здесь" if chat_id == target_id else "❌ не совпадает — /stat, /luz, /bonus и /games здесь не сработают"
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

    async def _handle_luz(message: Message) -> None:
        try:
            if message.chat.type == "channel":
                await message.answer(
                    "Команда /luz работает в группе (супергруппе), не в канале. "
                    "Вызовите её в чате, где собирается статистика."
                )
                return
            if message.chat.type == "private":
                target_id = _configured_stats_chat_id()
                await message.answer(
                    "Статистика лузеров доступна в общем чате команды, не в личке.\n\n"
                    f"Напишите /luz в чате с ID {target_id}.\n\n"
                    "Если в BotFather включён Group Privacy — используйте "
                    "/luz@имя_бота или отключите Privacy Mode."
                )
                return
            target_id = _configured_stats_chat_id()
            chat_id = int(message.chat.id)
            log.info("luz: chat_id=%s target_id=%s type=%s text=%r", chat_id, target_id, message.chat.type, message.text)
            if await _stats_chat_mismatch_reply(message, target_id, "/luz"):
                return
            async with db_lock:
                data = await losers_summary(conn, target_id)
            text = _format_luz_block(data)
            try:
                await message.answer(text)
            except TelegramBadRequest:
                await message.answer(text.replace("`", ""))
        except TelegramForbiddenError:
            log.warning("luz: bot cannot send messages in chat %s", message.chat.id)
        except Exception:
            log.exception("luz handler failed for chat %s", message.chat.id)
            try:
                await message.answer("Ошибка при открытии статистики лузеров. Проверьте логи wheel-bot на сервере.")
            except Exception:
                pass

    async def _handle_bonus(message: Message) -> None:
        try:
            if message.chat.type == "channel":
                await message.answer(
                    "Команда /bonus работает в группе (супергруппе), не в канале. "
                    "Вызовите её в чате статистики."
                )
                return
            if message.chat.type == "private":
                target_id = _configured_stats_chat_id()
                await message.answer(
                    "Попытка удачи доступна в общем чате команды, не в личке.\n\n"
                    f"Напишите /bonus в чате с ID {target_id}.\n\n"
                    "Если в BotFather включён Group Privacy — используйте "
                    "/bonus@имя_бота или отключите Privacy Mode."
                )
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            target_id = _configured_stats_chat_id()
            chat_id = int(message.chat.id)
            log.info("bonus: chat_id=%s user_id=%s", chat_id, user.id)
            if await _stats_chat_mismatch_reply(message, target_id, "/bonus"):
                return
            async with db_lock:
                result = await try_daily_bonus(conn, int(user.id), user_label=_bonus_user_label(message))
            user_label = _bonus_user_label(message)
            text = format_bonus_result(user_label, result)
            try:
                await message.reply(text, parse_mode="Markdown")
            except TelegramBadRequest:
                await message.reply(text.replace("*", ""))
            if result.get("status") == "win":
                await _notify_superadmins_bonus_win(
                    message,
                    settings,
                    user_label,
                    float(result["amount"]),
                    log,
                )
        except TelegramForbiddenError:
            log.warning("bonus: bot cannot send messages in chat %s", message.chat.id)
        except Exception:
            log.exception("bonus handler failed for chat %s", message.chat.id)
            try:
                await message.answer("Ошибка при попытке удачи. Проверьте логи wheel-bot на сервере.")
            except Exception:
                pass

    @router.message(lambda message: _is_bonus_command(message))
    async def bonus_cmd(message: Message) -> None:
        await _handle_bonus(message)

    async def _require_stats_group(message: Message, command: str) -> bool:
        """True если можно продолжать (группа + правильный chat_id)."""
        if message.chat.type == "channel":
            await message.answer(
                f"Команда {command} работает в группе (супергруппе), не в канале."
            )
            return False
        if message.chat.type == "private":
            target_id = _configured_stats_chat_id()
            await message.answer(
                f"Команда {command} доступна в общем чате команды.\n\n"
                f"Напишите в чате с ID {target_id}.\n\n"
                "Если включён Group Privacy — используйте "
                f"{command}@имя_бота или отключите Privacy Mode."
            )
            return False
        target_id = _configured_stats_chat_id()
        if await _stats_chat_mismatch_reply(message, target_id, command):
            return False
        return True

    async def _reply_md(message: Message, text: str) -> None:
        try:
            await message.reply(text, parse_mode="Markdown")
        except TelegramBadRequest:
            await message.reply(text.replace("*", ""))

    @router.message(lambda message: _is_games_command(message))
    async def games_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/games"):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            sub = _games_subcommand(message)
            if sub in ("help", "?", "start"):
                await _reply_md(message, format_games_welcome())
                return
            async with db_lock:
                if sub in ("me", "я", "my"):
                    data = await user_week_stats(conn, int(user.id))
                    await _reply_md(message, format_user_stats(data))
                    return
                data = await weekly_summary(conn, viewer_id=int(user.id))
            await _reply_md(message, format_leaderboard(data, viewer_id=int(user.id)))
        except TelegramForbiddenError:
            log.warning("games: no send permission in chat %s", message.chat.id)
        except Exception:
            log.exception("games handler failed")
            try:
                await message.answer("Ошибка /games. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(Command("games_welcome"))
    async def games_welcome_cmd(message: Message) -> None:
        """Superadmin: опубликовать инструкцию по мини-играм в чат."""
        try:
            tg_id = message.from_user.id if message.from_user else 0
            async with db_lock:
                role = await db.ensure_user(conn, tg_id)
            if role != "superadmin":
                await message.answer("Команда только для superadmin.")
                return
            if message.chat.type == "private":
                target_id = _configured_stats_chat_id()
                await message.bot.send_message(
                    target_id,
                    format_games_welcome().replace("*", ""),
                )
                await message.answer(f"Инструкция отправлена в чат {target_id}.")
                return
            if not await _require_stats_group(message, "/games_welcome"):
                return
            await _reply_md(message, format_games_welcome())
        except TelegramForbiddenError:
            log.warning("games_welcome: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("games_welcome failed")

    @router.message(lambda message: _is_slots_command(message))
    async def slots_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/slots"):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            label = _chat_user_label(user)
            async with db_lock:
                wait = await check_cooldown(conn, int(user.id), "slots")
            if wait:
                await message.reply(f"⏳ Следующий спин через {format_wait(wait)}")
                return
            dice_msg = await message.answer_dice(emoji="🎰")
            value = int(dice_msg.dice.value) if dice_msg.dice else 0
            points, flair = score_slots(value)
            async with db_lock:
                await record_play(
                    conn,
                    telegram_id=int(user.id),
                    user_label=label,
                    game_type="slots",
                    dice_value=value,
                    points=points,
                )
                rank = await get_user_rank(conn, int(user.id))
                wait = await check_cooldown(conn, int(user.id), "slots") or 0
            text = format_slots_result(
                label,
                value=value,
                points=points,
                flair=flair,
                rank=rank,
                wait_seconds=wait,
            )
            await dice_msg.reply(text)
        except TelegramForbiddenError:
            log.warning("slots: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("slots handler failed")
            try:
                await message.answer("Ошибка /slots. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_bowling_command(message))
    async def bowling_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/bowling"):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            label = _chat_user_label(user)
            async with db_lock:
                wait = await check_cooldown(conn, int(user.id), "bowling")
            if wait:
                await message.reply(f"⏳ Следующий бросок через {format_wait(wait)}")
                return
            dice_msg = await message.answer_dice(emoji="🎳")
            value = int(dice_msg.dice.value) if dice_msg.dice else 0
            points, flair = score_bowling(value)
            async with db_lock:
                await record_play(
                    conn,
                    telegram_id=int(user.id),
                    user_label=label,
                    game_type="bowling",
                    dice_value=value,
                    points=points,
                )
                rank = await get_user_rank(conn, int(user.id))
                wait = await check_cooldown(conn, int(user.id), "bowling") or 0
            text = format_bowling_result(
                label,
                value=value,
                points=points,
                flair=flair,
                rank=rank,
                wait_seconds=wait,
            )
            await dice_msg.reply(text)
        except TelegramForbiddenError:
            log.warning("bowling: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("bowling handler failed")
            try:
                await message.answer("Ошибка /bowling. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_duel_command(message))
    async def duel_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/duel"):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            reply = message.reply_to_message
            if not reply or not reply.dice:
                await message.reply(
                    "⚔️ Ответьте командой /duel на сообщение с 🎲 соперника "
                    "(он кидает кубик или вызывает вас на дуэль)."
                )
                return
            if reply.dice.emoji != "🎲":
                await message.reply("Нужно ответить на сообщение с обычным кубиком 🎲.")
                return
            opponent = reply.from_user
            if not opponent or opponent.is_bot:
                await message.reply("Дуэль только с живым игроком.")
                return
            if int(opponent.id) == int(user.id):
                await message.reply("С собой дуэлиться бессмысленно 😄")
                return
            challenger_label = _chat_user_label(user)
            opponent_label = _chat_user_label(opponent)
            opponent_value = int(reply.dice.value)
            async with db_lock:
                wait = await check_cooldown(conn, int(user.id), "duel")
            if wait:
                await message.reply(f"⏳ Следующая дуэль через {format_wait(wait)}")
                return
            dice_msg = await message.answer_dice(emoji="🎲")
            challenger_value = int(dice_msg.dice.value) if dice_msg.dice else 0
            if challenger_value > opponent_value:
                c_pts, o_pts = 25, 5
                c_out, o_out = "win", "loss"
                winner = "challenger"
            elif challenger_value < opponent_value:
                c_pts, o_pts = 5, 25
                c_out, o_out = "loss", "win"
                winner = "opponent"
            else:
                c_pts = o_pts = 12
                c_out = o_out = "tie"
                winner = "tie"
            async with db_lock:
                await record_play(
                    conn,
                    telegram_id=int(user.id),
                    user_label=challenger_label,
                    game_type="duel",
                    dice_value=challenger_value,
                    points=c_pts,
                    meta={
                        "opponent_id": int(opponent.id),
                        "opponent_label": opponent_label,
                        "opponent_value": opponent_value,
                        "outcome": c_out,
                    },
                )
                await record_play(
                    conn,
                    telegram_id=int(opponent.id),
                    user_label=opponent_label,
                    game_type="duel",
                    dice_value=opponent_value,
                    points=o_pts,
                    meta={
                        "opponent_id": int(user.id),
                        "opponent_label": challenger_label,
                        "opponent_value": challenger_value,
                        "outcome": o_out,
                    },
                )
            text = format_duel_result(
                challenger_label=challenger_label,
                opponent_label=opponent_label,
                challenger_value=challenger_value,
                opponent_value=opponent_value,
                challenger_points=c_pts,
                opponent_points=o_pts,
                winner=winner,
            )
            await dice_msg.reply(text)
        except TelegramForbiddenError:
            log.warning("duel: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("duel handler failed")
            try:
                await message.answer("Ошибка /duel. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_luz_command(message))
    async def luz_cmd(message: Message) -> None:
        await _handle_luz(message)

    @router.message(lambda message: _winreport_nick(message) is not None)
    async def winreport_cmd(message: Message) -> None:
        nick_query = _winreport_nick(message)
        if not nick_query:
            return
        try:
            if message.chat.type == "channel":
                await message.answer("Команда /winreport работает в группе, не в канале.")
                return
            if message.chat.type == "private":
                await message.answer("Напишите /winreport <ник> в чате статистики.")
                return
            target_id = _configured_stats_chat_id()
            if await _stats_chat_mismatch_reply(message, target_id, "/winreport"):
                return
            async with db_lock:
                data = await participant_wheel_wins(conn, target_id, nick_query)
            if data is None:
                await message.answer(f"Участник не найден: {nick_query}")
                return
            await message.answer(_format_winreport_block(data))
        except TelegramForbiddenError:
            log.warning("winreport: no send permission in chat %s", message.chat.id)
        except Exception:
            log.exception("winreport failed for chat %s", message.chat.id)
            try:
                await message.answer("Ошибка при формировании отчёта. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _first_command_token(message.text or "") == "/winreport")
    async def winreport_help(message: Message) -> None:
        if message.chat.type not in ("group", "supergroup"):
            return
        target_id = _configured_stats_chat_id()
        if int(message.chat.id) != int(target_id):
            return
        await message.answer("Использование: /winreport <ник>\nНапример: /winreport Регуляр кэшбеков")

    @router.message(lambda message: _is_stat_command(message))
    async def stats_cmd(message: Message) -> None:
        await _handle_stat(message)

    @router.message(lambda m: bool(m.text and m.text.strip().lower() == "статистика"))
    async def stats_text(message: Message) -> None:
        await _handle_stat(message)

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
