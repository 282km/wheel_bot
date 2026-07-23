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
from wheel_bot.blackjack_service import (
    BlackjackUiState,
    BlackjackView,
    clear_session,
    get_session,
    get_ui_state,
    hit_blackjack,
    save_ui_state,
    set_board_message_id,
    stand_blackjack,
    start_blackjack,
)
from wheel_bot.dogslot_service import (
    DogslotUiState,
    DogslotView,
    clear_session as clear_dogslot_session,
    dogslot_action,
    get_session as get_dogslot_session,
    get_ui_state as get_dogslot_ui_state,
    save_ui_state as save_dogslot_ui_state,
    set_board_message_id as set_dogslot_board_message_id,
    spin_dogslot,
)
from wheel_bot.mines_service import (
    GRID_SIZE,
    MinesUiState,
    MinesView,
    cashout_mines,
    clear_session as clear_mines_session,
    get_session as get_mines_session,
    get_ui_state as get_mines_ui_state,
    open_mines_cell,
    save_ui_state as save_mines_ui_state,
    set_board_message_id as set_mines_board_message_id,
    start_mines,
)
from wheel_bot.config import Settings
from wheel_bot.feature_flags import (
    features_status,
    mini_games_enabled,
    set_mini_games_enabled,
)
from wheel_bot.game_messages import (
    format_games_welcome,
    format_leaderboard,
    format_user_stats,
)
from wheel_bot.game_service import user_week_stats, weekly_summary
from wheel_bot.user_labels import plain_player_label, remember_telegram_user
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


def _blackjack_keyboard(owner_id: int) -> InlineKeyboardMarkup:
    oid = int(owner_id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🃏 Hit", callback_data=f"bj:hit:{oid}"),
                InlineKeyboardButton(text="✋ Stand", callback_data=f"bj:stand:{oid}"),
            ]
        ]
    )


def _mines_keyboard(view: MinesView, owner_id: int) -> InlineKeyboardMarkup | None:
    if view.finished:
        return None
    oid = int(owner_id)
    rows: list[list[InlineKeyboardButton]] = []
    for r in range(GRID_SIZE):
        row: list[InlineKeyboardButton] = []
        for c in range(GRID_SIZE):
            idx = r * GRID_SIZE + c
            if idx in view.opened:
                row.append(InlineKeyboardButton(text="💎", callback_data=f"ms:x:{oid}:{idx}"))
            else:
                row.append(InlineKeyboardButton(text="⬜", callback_data=f"ms:o:{oid}:{idx}"))
        rows.append(row)
    if view.can_cashout:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"💰 Забрать ×{view.multiplier:.2f}",
                    callback_data=f"ms:c:{oid}",
                )
            ]
        )
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
            lines.append(f"👤 {plain_player_label(str(row['label']), default='Участник')}")
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


def _is_blackjack_command(message: Message) -> bool:
    if not message.text:
        return False
    token = _first_command_token(message.text)
    return token in ("/blackjack", "/bj")


def _is_hit_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/hit"


def _is_stand_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/stand"


def _is_mines_command(message: Message) -> bool:
    if not message.text:
        return False
    token = _first_command_token(message.text)
    return token in ("/mines", "/min")


def _is_cash_command(message: Message) -> bool:
    if not message.text:
        return False
    return _first_command_token(message.text) == "/cash"


def _mines_mine_count(message: Message) -> int:
    if not message.text:
        return 3
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return 3
    try:
        count = int(parts[1].strip())
    except ValueError:
        return 3
    return count if count in (3, 5, 10) else 3


def _is_dogslot_command(message: Message) -> bool:
    if not message.text:
        return False
    token = _first_command_token(message.text)
    return token in ("/dogslot", "/dog")


def _dogslot_keyboard(view: DogslotView, owner_id: int) -> InlineKeyboardMarkup | None:
    if not view.can_act or view.action == "none":
        return None
    oid = int(owner_id)
    if view.action == "pick":
        label, data = "🎁 Раскрутить 3×3", f"dh:p:{oid}"
    elif view.action == "free_spin":
        label, data = "🎰 Free Spin", f"dh:f:{oid}"
    else:
        label, data = "🎰 Spin", f"dh:s:{oid}"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, callback_data=data)]]
    )


def _games_subcommand(message: Message) -> str | None:
    if not message.text:
        return None
    parts = message.text.strip().split(maxsplit=1)
    if len(parts) < 2:
        return None
    return parts[1].strip().lower()


async def _bonus_user_label_saved(message: Message, conn: aiosqlite.Connection) -> str:
    user = message.from_user
    if not user:
        return "Игрок"
    return await remember_telegram_user(conn, user)


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
                mode = "сводка блэкджека" if post.source_mode == "games" else post.source_mode
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
            await message.answer(
                "Утренняя сводка выключена.\n"
                "Включить (superadmin): /morning_on\n"
                "Статус: /features"
            )
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
                "Бонус удачи — /bonus (раз в сутки в чате статистики).",
                reply_markup=_admin_webapp_keyboard(settings),
            )
            return
        await message.answer(
            "В общем чате: /stat или «Статистика», /luz — статистика лузеров, "
            "/bonus — попытка удачи раз в сутки."
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
                user_label = await _bonus_user_label_saved(message, conn)
                result = await try_daily_bonus(conn, int(user.id), user_label=user_label)
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

    async def _cleanup_blackjack_messages(
        bot,
        telegram_id: int,
        chat_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_ui_state(conn, int(telegram_id))
            session = await get_session(conn, int(telegram_id))
            if session is not None:
                await clear_session(conn, int(telegram_id))

        to_delete: list[int] = []
        if ui:
            if ui.board_message_id is not None:
                to_delete.append(int(ui.board_message_id))
            if ui.command_message_id is not None:
                to_delete.append(int(ui.command_message_id))
        if session and session.message_id is not None:
            mid = int(session.message_id)
            if mid not in to_delete:
                to_delete.append(mid)

        for mid in to_delete:
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=mid)
            except TelegramBadRequest:
                pass
            except Exception:
                log.debug("bj cleanup delete failed chat=%s msg=%s", chat_id, mid, exc_info=True)

        async with db_lock:
            await save_ui_state(
                conn,
                BlackjackUiState(telegram_id=int(telegram_id), chat_id=int(chat_id)),
            )

    async def _remember_blackjack_command(
        telegram_id: int,
        chat_id: int,
        command_message_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_ui_state(conn, int(telegram_id))
            await save_ui_state(
                conn,
                BlackjackUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=ui.board_message_id if ui else None,
                    command_message_id=int(command_message_id),
                ),
            )

    async def _remember_blackjack_board(telegram_id: int, chat_id: int, board_message_id: int) -> None:
        async with db_lock:
            ui = await get_ui_state(conn, int(telegram_id))
            cmd_mid = ui.command_message_id if ui else None
            await save_ui_state(
                conn,
                BlackjackUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=int(board_message_id),
                    command_message_id=cmd_mid,
                ),
            )

    async def _send_blackjack_view(
        message: Message,
        view: BlackjackView,
        *,
        owner_id: int,
        edit_message: Message | None = None,
        board_message_id: int | None = None,
    ) -> int | None:
        markup = None if view.finished else _blackjack_keyboard(owner_id)
        text = view.text
        chat_id = int(message.chat.id)
        result_id: int | None = board_message_id

        async def _apply_edit(target_message_id: int) -> None:
            nonlocal result_id
            await message.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=target_message_id,
                parse_mode="Markdown",
                reply_markup=markup,
            )
            result_id = target_message_id

        try:
            if edit_message is not None:
                await edit_message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(edit_message.message_id)
            elif board_message_id is not None:
                await _apply_edit(board_message_id)
            else:
                sent = await message.answer(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(sent.message_id)
                if not view.finished:
                    async with db_lock:
                        await set_board_message_id(conn, owner_id, result_id)
        except TelegramBadRequest:
            plain = text.replace("*", "")
            try:
                if edit_message is not None:
                    await edit_message.edit_text(plain, reply_markup=markup)
                    result_id = int(edit_message.message_id)
                elif board_message_id is not None:
                    await message.bot.edit_message_text(
                        plain,
                        chat_id=chat_id,
                        message_id=board_message_id,
                        reply_markup=markup,
                    )
                    result_id = board_message_id
                else:
                    sent = await message.answer(plain, reply_markup=markup)
                    result_id = int(sent.message_id)
                    if not view.finished:
                        async with db_lock:
                            await set_board_message_id(conn, owner_id, result_id)
            except TelegramBadRequest:
                if not view.finished:
                    sent = await message.answer(plain, reply_markup=markup)
                    result_id = int(sent.message_id)
                    async with db_lock:
                        await set_board_message_id(conn, owner_id, result_id)
                else:
                    sent = await message.answer(plain)
                    result_id = int(sent.message_id)

        if result_id is not None:
            await _remember_blackjack_board(owner_id, chat_id, result_id)
        return result_id

    async def _cleanup_mines_messages(
        bot,
        telegram_id: int,
        chat_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_mines_ui_state(conn, int(telegram_id))
            session = await get_mines_session(conn, int(telegram_id))
            if session is not None:
                await clear_mines_session(conn, int(telegram_id))

        to_delete: list[int] = []
        if ui:
            if ui.board_message_id is not None:
                to_delete.append(int(ui.board_message_id))
            if ui.command_message_id is not None:
                to_delete.append(int(ui.command_message_id))
        if session and session.message_id is not None:
            mid = int(session.message_id)
            if mid not in to_delete:
                to_delete.append(mid)

        for mid in to_delete:
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=mid)
            except TelegramBadRequest:
                pass
            except Exception:
                log.debug("mines cleanup delete failed chat=%s msg=%s", chat_id, mid, exc_info=True)

        async with db_lock:
            await save_mines_ui_state(
                conn,
                MinesUiState(telegram_id=int(telegram_id), chat_id=int(chat_id)),
            )

    async def _remember_mines_command(
        telegram_id: int,
        chat_id: int,
        command_message_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_mines_ui_state(conn, int(telegram_id))
            await save_mines_ui_state(
                conn,
                MinesUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=ui.board_message_id if ui else None,
                    command_message_id=int(command_message_id),
                ),
            )

    async def _remember_mines_board(telegram_id: int, chat_id: int, board_message_id: int) -> None:
        async with db_lock:
            ui = await get_mines_ui_state(conn, int(telegram_id))
            cmd_mid = ui.command_message_id if ui else None
            await save_mines_ui_state(
                conn,
                MinesUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=int(board_message_id),
                    command_message_id=cmd_mid,
                ),
            )

    async def _send_mines_view(
        message: Message,
        view: MinesView,
        *,
        owner_id: int,
        edit_message: Message | None = None,
        board_message_id: int | None = None,
    ) -> int | None:
        markup = _mines_keyboard(view, owner_id)
        text = view.text
        chat_id = int(message.chat.id)
        result_id: int | None = board_message_id

        async def _apply_edit(target_message_id: int) -> None:
            nonlocal result_id
            await message.bot.edit_message_text(
                text,
                chat_id=chat_id,
                message_id=target_message_id,
                parse_mode="Markdown",
                reply_markup=markup,
            )
            result_id = target_message_id

        try:
            if edit_message is not None:
                await edit_message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(edit_message.message_id)
            elif board_message_id is not None:
                await _apply_edit(board_message_id)
            else:
                sent = await message.answer(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(sent.message_id)
                if not view.finished:
                    async with db_lock:
                        await set_mines_board_message_id(conn, owner_id, result_id)
        except TelegramBadRequest:
            plain = text.replace("*", "")
            try:
                if edit_message is not None:
                    await edit_message.edit_text(plain, reply_markup=markup)
                    result_id = int(edit_message.message_id)
                elif board_message_id is not None:
                    await message.bot.edit_message_text(
                        plain,
                        chat_id=chat_id,
                        message_id=board_message_id,
                        reply_markup=markup,
                    )
                    result_id = board_message_id
                else:
                    sent = await message.answer(plain, reply_markup=markup)
                    result_id = int(sent.message_id)
                    if not view.finished:
                        async with db_lock:
                            await set_mines_board_message_id(conn, owner_id, result_id)
            except TelegramBadRequest:
                if not view.finished:
                    sent = await message.answer(plain, reply_markup=markup)
                    result_id = int(sent.message_id)
                    async with db_lock:
                        await set_mines_board_message_id(conn, owner_id, result_id)
                else:
                    sent = await message.answer(plain)
                    result_id = int(sent.message_id)

        if result_id is not None:
            await _remember_mines_board(owner_id, chat_id, result_id)
        return result_id

    async def _run_mines_cashout(message: Message, uid: int, label: str) -> None:
        async with db_lock:
            session = await get_mines_session(conn, uid)
            board_mid = session.message_id if session else None
            err, view = await cashout_mines(
                conn,
                telegram_id=uid,
                user_label=label,
            )
        if err:
            await _reply_md(message, err)
            return
        if view:
            await _send_mines_view(
                message,
                view,
                owner_id=uid,
                board_message_id=board_mid,
            )

    async def _cleanup_dogslot_messages(
        bot,
        telegram_id: int,
        chat_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_dogslot_ui_state(conn, int(telegram_id))
            session = await get_dogslot_session(conn, int(telegram_id))
            if session is not None:
                await clear_dogslot_session(conn, int(telegram_id))

        to_delete: list[int] = []
        if ui:
            if ui.board_message_id is not None:
                to_delete.append(int(ui.board_message_id))
            if ui.command_message_id is not None:
                to_delete.append(int(ui.command_message_id))
        if session and session.message_id is not None:
            mid = int(session.message_id)
            if mid not in to_delete:
                to_delete.append(mid)

        for mid in to_delete:
            try:
                await bot.delete_message(chat_id=int(chat_id), message_id=mid)
            except TelegramBadRequest:
                pass
            except Exception:
                log.debug("dogslot cleanup delete failed chat=%s msg=%s", chat_id, mid, exc_info=True)

        async with db_lock:
            await save_dogslot_ui_state(
                conn,
                DogslotUiState(telegram_id=int(telegram_id), chat_id=int(chat_id)),
            )

    async def _remember_dogslot_command(
        telegram_id: int,
        chat_id: int,
        command_message_id: int,
    ) -> None:
        async with db_lock:
            ui = await get_dogslot_ui_state(conn, int(telegram_id))
            await save_dogslot_ui_state(
                conn,
                DogslotUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=ui.board_message_id if ui else None,
                    command_message_id=int(command_message_id),
                ),
            )

    async def _remember_dogslot_board(telegram_id: int, chat_id: int, board_message_id: int) -> None:
        async with db_lock:
            ui = await get_dogslot_ui_state(conn, int(telegram_id))
            cmd_mid = ui.command_message_id if ui else None
            await save_dogslot_ui_state(
                conn,
                DogslotUiState(
                    telegram_id=int(telegram_id),
                    chat_id=int(chat_id),
                    board_message_id=int(board_message_id),
                    command_message_id=cmd_mid,
                ),
            )

    async def _send_dogslot_view(
        message: Message,
        view: DogslotView,
        *,
        owner_id: int,
        edit_message: Message | None = None,
        board_message_id: int | None = None,
    ) -> int | None:
        markup = _dogslot_keyboard(view, owner_id)
        text = view.text
        chat_id = int(message.chat.id)
        result_id: int | None = board_message_id

        try:
            if edit_message is not None:
                await edit_message.edit_text(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(edit_message.message_id)
            elif board_message_id is not None:
                await message.bot.edit_message_text(
                    text,
                    chat_id=chat_id,
                    message_id=board_message_id,
                    parse_mode="Markdown",
                    reply_markup=markup,
                )
                result_id = board_message_id
            else:
                sent = await message.answer(text, parse_mode="Markdown", reply_markup=markup)
                result_id = int(sent.message_id)
                async with db_lock:
                    session = await get_dogslot_session(conn, owner_id)
                    if session is not None:
                        await set_dogslot_board_message_id(conn, owner_id, result_id)
        except TelegramBadRequest:
            plain = text.replace("*", "")
            try:
                if edit_message is not None:
                    await edit_message.edit_text(plain, reply_markup=markup)
                    result_id = int(edit_message.message_id)
                elif board_message_id is not None:
                    await message.bot.edit_message_text(
                        plain,
                        chat_id=chat_id,
                        message_id=board_message_id,
                        reply_markup=markup,
                    )
                    result_id = board_message_id
                else:
                    sent = await message.answer(plain, reply_markup=markup)
                    result_id = int(sent.message_id)
            except TelegramBadRequest:
                sent = await message.answer(plain, reply_markup=markup)
                result_id = int(sent.message_id)

        if result_id is not None:
            await _remember_dogslot_board(owner_id, chat_id, result_id)
        return result_id

    async def _user_role(telegram_id: int) -> str:
        async with db_lock:
            return await db.ensure_user(conn, telegram_id)

    async def _require_superadmin_private(message: Message) -> bool:
        if message.chat.type != "private":
            await message.answer("Команда только в личке с ботом (superadmin).")
            return False
        tg_id = message.from_user.id if message.from_user else 0
        role = await _user_role(tg_id)
        if role != "superadmin":
            await message.answer("Команда только для superadmin.")
            return False
        return True

    @router.message(Command("features"))
    async def features_cmd(message: Message) -> None:
        if not await _require_superadmin_private(message):
            return
        async with db_lock:
            st = await features_status(conn, settings)
        games = "✅ включены" if st["games"] else "❌ выключены"
        morning = "✅ включена" if st["morning"] else "❌ выключена"
        await message.answer(
            "⚙️ Функции бота\n\n"
            f"🎮 Мини-игры (BJ + мины + Dog House): {games}\n"
            f"☀️ Утренняя сводка: {morning}\n\n"
            "Включить:\n"
            "/games_on — все мини-игры\n"
            "/morning_on — утро в 8:00 (час в WebApp)\n\n"
            "Выключить:\n"
            "/games_off · /morning_off"
        )

    @router.message(Command("games_on"))
    async def games_on_cmd(message: Message) -> None:
        if not await _require_superadmin_private(message):
            return
        async with db_lock:
            await set_mini_games_enabled(conn, True)
        await message.answer(
            "🎮 Мини-игры включены (BJ + мины + Dog House).\n"
            "В чате: /games help · /games_welcome — инструкция для всех.\n"
            "🃏 /blackjack · 💣 /mines · 🐶 /dogslot"
        )

    @router.message(Command("games_off"))
    async def games_off_cmd(message: Message) -> None:
        if not await _require_superadmin_private(message):
            return
        async with db_lock:
            await set_mini_games_enabled(conn, False)
        await message.answer("🎮 Мини-игры выключены.")

    @router.message(Command("morning_on"))
    async def morning_on_cmd(message: Message) -> None:
        if not await _require_superadmin_private(message):
            return
        from wheel_bot.morning_digest_settings import save_morning_digest_settings

        async with db_lock:
            cfg = await save_morning_digest_settings(conn, settings, enabled=True)
        await message.answer(
            f"☀️ Утренняя сводка включена ({cfg.hour}:00 {cfg.timezone}).\n"
            "Тест: /morning_test · Принудительно в чат: /morning_send"
        )

    @router.message(Command("morning_off"))
    async def morning_off_cmd(message: Message) -> None:
        if not await _require_superadmin_private(message):
            return
        from wheel_bot.morning_digest_settings import save_morning_digest_settings

        async with db_lock:
            await save_morning_digest_settings(conn, settings, enabled=False)
        await message.answer("☀️ Утренняя сводка выключена.")

    async def _require_mini_games(message: Message) -> bool:
        async with db_lock:
            enabled = await mini_games_enabled(conn, settings)
        if enabled:
            return True
        tg_id = message.from_user.id if message.from_user else 0
        role = await _user_role(tg_id)
        if role == "superadmin":
            await message.reply(
                "🎮 Мини-игры выключены.\n"
                "Включить в личке: /games_on"
            )
        else:
            await message.reply("🎮 Мини-игры сейчас выключены.")
        return False

    @router.message(lambda message: _is_games_command(message))
    async def games_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/games"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            async with db_lock:
                await remember_telegram_user(conn, user)
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
        """Superadmin: опубликовать инструкцию по блэкджеку в чат."""
        try:
            tg_id = message.from_user.id if message.from_user else 0
            async with db_lock:
                role = await db.ensure_user(conn, tg_id)
            if role != "superadmin":
                await message.answer("Команда только для superadmin.")
                return
            async with db_lock:
                if not await mini_games_enabled(conn, settings):
                    await message.answer(
                        "Блэкджек и мины выключены. Сначала: /games_on"
                    )
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

    @router.message(lambda message: _is_blackjack_command(message))
    async def blackjack_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/blackjack"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            chat_id = int(message.chat.id)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
            await _cleanup_blackjack_messages(message.bot, uid, chat_id)
            async with db_lock:
                err, view = await start_blackjack(
                    conn,
                    telegram_id=uid,
                    user_label=label,
                    chat_id=chat_id,
                )
            if err:
                await _reply_md(message, err)
                return
            if view:
                await _send_blackjack_view(message, view, owner_id=uid)
                await _remember_blackjack_command(uid, chat_id, int(message.message_id))
        except TelegramForbiddenError:
            log.warning("blackjack: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("blackjack handler failed")
            try:
                await message.answer("Ошибка /blackjack. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_hit_command(message))
    async def hit_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/hit"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_session(conn, uid)
                board_mid = session.message_id if session else None
                err, view = await hit_blackjack(
                    conn,
                    telegram_id=uid,
                    user_label=label,
                )
            if err:
                await _reply_md(message, err)
                return
            if view:
                await _send_blackjack_view(
                    message,
                    view,
                    owner_id=uid,
                    board_message_id=board_mid,
                )
            try:
                await message.bot.delete_message(chat_id=int(message.chat.id), message_id=int(message.message_id))
            except TelegramBadRequest:
                pass
        except TelegramForbiddenError:
            log.warning("hit: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("hit handler failed")
            try:
                await message.answer("Ошибка /hit. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_stand_command(message))
    async def stand_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/stand"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_session(conn, uid)
                board_mid = session.message_id if session else None
                err, view = await stand_blackjack(
                    conn,
                    telegram_id=uid,
                    user_label=label,
                )
            if err:
                await _reply_md(message, err)
                return
            if view:
                await _send_blackjack_view(
                    message,
                    view,
                    owner_id=uid,
                    board_message_id=board_mid,
                )
            try:
                await message.bot.delete_message(chat_id=int(message.chat.id), message_id=int(message.message_id))
            except TelegramBadRequest:
                pass
        except TelegramForbiddenError:
            log.warning("stand: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("stand handler failed")
            try:
                await message.answer("Ошибка /stand. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.callback_query(F.data.startswith("bj:"))
    async def blackjack_callback(cb: CallbackQuery) -> None:
        try:
            user = cb.from_user
            if not user or user.is_bot:
                await cb.answer()
                return
            if not cb.message:
                await cb.answer()
                return

            parts = (cb.data or "").split(":")
            if len(parts) != 3:
                await cb.answer("Устаревшие кнопки. Начните /blackjack", show_alert=True)
                return
            action, owner_raw = parts[1], parts[2]
            if action not in ("hit", "stand"):
                await cb.answer()
                return
            try:
                owner_id = int(owner_raw)
            except ValueError:
                await cb.answer("Устаревшие кнопки. Начните /blackjack", show_alert=True)
                return
            if int(user.id) != owner_id:
                await cb.answer("Это партия другого игрока.", show_alert=True)
                return

            async with db_lock:
                enabled = await mini_games_enabled(conn, settings)
            if not enabled:
                role = await _user_role(int(user.id))
                if role != "superadmin":
                    await cb.answer("Мини-игры выключены.", show_alert=True)
                    return
            target_id = _configured_stats_chat_id()
            if int(cb.message.chat.id) != int(target_id):
                await cb.answer("Кнопки работают только в чате статистики.", show_alert=True)
                return

            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_session(conn, owner_id)
                if session is None:
                    await cb.answer("Партия уже завершена.", show_alert=True)
                    return
                if session.message_id and cb.message.message_id != session.message_id:
                    await cb.answer(
                        "Это старая доска. Используйте актуальное сообщение своей партии.",
                        show_alert=True,
                    )
                    return
                if int(session.chat_id) != int(cb.message.chat.id):
                    await cb.answer("Партия привязана к другому чату.", show_alert=True)
                    return
                if action == "hit":
                    err, view = await hit_blackjack(
                        conn,
                        telegram_id=owner_id,
                        user_label=label,
                    )
                else:
                    err, view = await stand_blackjack(
                        conn,
                        telegram_id=owner_id,
                        user_label=label,
                    )

            if err:
                await cb.answer(err.replace("`", "").replace("*", ""), show_alert=True)
                return
            if not view:
                await cb.answer()
                return
            await cb.answer()
            await _send_blackjack_view(
                cb.message,
                view,
                owner_id=owner_id,
                edit_message=cb.message,
            )
        except TelegramForbiddenError:
            log.warning("bj callback: forbidden in chat %s", cb.message.chat.id if cb.message else "?")
            await cb.answer()
        except Exception:
            log.exception("blackjack callback failed")
            try:
                await cb.answer("Ошибка. Попробуйте /blackjack", show_alert=True)
            except Exception:
                pass

    @router.message(lambda message: _is_mines_command(message))
    async def mines_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/mines"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            chat_id = int(message.chat.id)
            mine_count = _mines_mine_count(message)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
            await _cleanup_mines_messages(message.bot, uid, chat_id)
            async with db_lock:
                err, view = await start_mines(
                    conn,
                    telegram_id=uid,
                    user_label=label,
                    chat_id=chat_id,
                    mine_count=mine_count,
                )
            if err:
                await _reply_md(message, err)
                return
            if view:
                await _send_mines_view(message, view, owner_id=uid)
                await _remember_mines_command(uid, chat_id, int(message.message_id))
        except TelegramForbiddenError:
            log.warning("mines: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("mines handler failed")
            try:
                await message.answer("Ошибка /mines. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.message(lambda message: _is_cash_command(message))
    async def cash_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/cash"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
            await _run_mines_cashout(message, uid, label)
            try:
                await message.bot.delete_message(chat_id=int(message.chat.id), message_id=int(message.message_id))
            except TelegramBadRequest:
                pass
        except TelegramForbiddenError:
            log.warning("cash: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("cash handler failed")
            try:
                await message.answer("Ошибка /cash. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.callback_query(F.data.startswith("ms:"))
    async def mines_callback(cb: CallbackQuery) -> None:
        try:
            user = cb.from_user
            if not user or user.is_bot:
                await cb.answer()
                return
            if not cb.message:
                await cb.answer()
                return

            parts = (cb.data or "").split(":")
            if len(parts) < 3:
                await cb.answer("Устаревшие кнопки. Начните /mines", show_alert=True)
                return
            action, owner_raw = parts[1], parts[2]
            if action not in ("o", "c", "x"):
                await cb.answer()
                return
            try:
                owner_id = int(owner_raw)
            except ValueError:
                await cb.answer("Устаревшие кнопки. Начните /mines", show_alert=True)
                return
            if int(user.id) != owner_id:
                await cb.answer("Это игра другого игрока.", show_alert=True)
                return

            if action == "x":
                await cb.answer("Клетка уже открыта.")
                return

            async with db_lock:
                enabled = await mini_games_enabled(conn, settings)
            if not enabled:
                role = await _user_role(int(user.id))
                if role != "superadmin":
                    await cb.answer("Мини-игры выключены.", show_alert=True)
                    return
            target_id = _configured_stats_chat_id()
            if int(cb.message.chat.id) != int(target_id):
                await cb.answer("Кнопки работают только в чате статистики.", show_alert=True)
                return

            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_mines_session(conn, owner_id)
                if session is None:
                    await cb.answer("Игра уже завершена.", show_alert=True)
                    return
                if session.message_id and cb.message.message_id != session.message_id:
                    await cb.answer(
                        "Это старое поле. Используйте актуальное сообщение своей игры.",
                        show_alert=True,
                    )
                    return
                if int(session.chat_id) != int(cb.message.chat.id):
                    await cb.answer("Игра привязана к другому чату.", show_alert=True)
                    return
                if action == "c":
                    err, view = await cashout_mines(
                        conn,
                        telegram_id=owner_id,
                        user_label=label,
                    )
                else:
                    if len(parts) != 4:
                        await cb.answer("Устаревшие кнопки.", show_alert=True)
                        return
                    try:
                        cell = int(parts[3])
                    except ValueError:
                        await cb.answer("Устаревшие кнопки.", show_alert=True)
                        return
                    err, view = await open_mines_cell(
                        conn,
                        telegram_id=owner_id,
                        user_label=label,
                        cell=cell,
                    )

            if err:
                await cb.answer(err.replace("`", "").replace("*", ""), show_alert=True)
                return
            if not view:
                await cb.answer()
                return
            await cb.answer()
            await _send_mines_view(
                cb.message,
                view,
                owner_id=owner_id,
                edit_message=cb.message,
            )
        except TelegramForbiddenError:
            log.warning("mines callback: forbidden in chat %s", cb.message.chat.id if cb.message else "?")
            await cb.answer()
        except Exception:
            log.exception("mines callback failed")
            try:
                await cb.answer("Ошибка. Попробуйте /mines", show_alert=True)
            except Exception:
                pass

    @router.message(lambda message: _is_dogslot_command(message))
    async def dogslot_cmd(message: Message) -> None:
        try:
            if not await _require_stats_group(message, "/dogslot"):
                return
            if not await _require_mini_games(message):
                return
            user = message.from_user
            if not user or user.is_bot:
                return
            uid = int(user.id)
            chat_id = int(message.chat.id)
            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_dogslot_session(conn, uid)
            if session is None:
                await _cleanup_dogslot_messages(message.bot, uid, chat_id)
            async with db_lock:
                err, view = await spin_dogslot(
                    conn,
                    telegram_id=uid,
                    user_label=label,
                    chat_id=chat_id,
                )
            if err:
                await _reply_md(message, err)
                return
            if view:
                await _send_dogslot_view(message, view, owner_id=uid)
                if session is None:
                    await _remember_dogslot_command(uid, chat_id, int(message.message_id))
        except TelegramForbiddenError:
            log.warning("dogslot: forbidden in chat %s", message.chat.id)
        except Exception:
            log.exception("dogslot handler failed")
            try:
                await message.answer("Ошибка /dogslot. Проверьте логи wheel-bot.")
            except Exception:
                pass

    @router.callback_query(F.data.startswith("dh:"))
    async def dogslot_callback(cb: CallbackQuery) -> None:
        try:
            user = cb.from_user
            if not user or user.is_bot:
                await cb.answer()
                return
            if not cb.message:
                await cb.answer()
                return

            parts = (cb.data or "").split(":")
            if len(parts) != 3:
                await cb.answer("Устаревшие кнопки. Начните /dogslot", show_alert=True)
                return
            action, owner_raw = parts[1], parts[2]
            if action not in ("s", "p", "f"):
                await cb.answer()
                return
            try:
                owner_id = int(owner_raw)
            except ValueError:
                await cb.answer("Устаревшие кнопки. Начните /dogslot", show_alert=True)
                return
            if int(user.id) != owner_id:
                await cb.answer("Это слот другого игрока.", show_alert=True)
                return

            async with db_lock:
                enabled = await mini_games_enabled(conn, settings)
            if not enabled:
                role = await _user_role(int(user.id))
                if role != "superadmin":
                    await cb.answer("Мини-игры выключены.", show_alert=True)
                    return
            target_id = _configured_stats_chat_id()
            if int(cb.message.chat.id) != int(target_id):
                await cb.answer("Кнопки работают только в чате статистики.", show_alert=True)
                return

            action_map = {"s": "spin", "p": "pick", "f": "free_spin"}
            async with db_lock:
                label = await remember_telegram_user(conn, user)
                session = await get_dogslot_session(conn, owner_id)
                ui = await get_dogslot_ui_state(conn, owner_id)
                expected_board = None
                if session and session.message_id:
                    expected_board = int(session.message_id)
                elif ui and ui.board_message_id:
                    expected_board = int(ui.board_message_id)
                if expected_board is not None and cb.message.message_id != expected_board:
                    await cb.answer(
                        "Это старое поле. Используйте актуальное сообщение своего слота.",
                        show_alert=True,
                    )
                    return
                err, view = await dogslot_action(
                    conn,
                    telegram_id=owner_id,
                    user_label=label,
                    chat_id=int(cb.message.chat.id),
                    action=action_map[action],  # type: ignore[arg-type]
                )

            if err:
                await cb.answer(err.replace("`", "").replace("*", ""), show_alert=True)
                return
            if not view:
                await cb.answer()
                return
            await cb.answer()
            await _send_dogslot_view(
                cb.message,
                view,
                owner_id=owner_id,
                edit_message=cb.message,
            )
        except TelegramForbiddenError:
            log.warning("dogslot callback: forbidden in chat %s", cb.message.chat.id if cb.message else "?")
            await cb.answer()
        except Exception:
            log.exception("dogslot callback failed")
            try:
                await cb.answer("Ошибка. Попробуйте /dogslot", show_alert=True)
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
