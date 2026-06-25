from __future__ import annotations

from typing import Any

from wheel_bot.bonus_service import BONUS_WIN_ODDS, format_bonus_wait


def _fmt_money(x: float) -> str:
    s = f"{x:g}"
    return f"${s.replace('.', ',')}"


def format_bonus_cooldown(user_label: str, wait_seconds: int) -> str:
    wait_text = format_bonus_wait(wait_seconds)
    return (
        f"⏳ *Удача на перерыве*\n\n"
        f"👤 {user_label}, вы уже пробовали удачу.\n\n"
        f"🕐 Следующая попытка через *{wait_text}*.\n\n"
        f"🍀 Возвращайтесь позже — шанс всё ещё *1 к {BONUS_WIN_ODDS:,}*!".replace(",", " ")
    )


def format_bonus_lose(user_label: str) -> str:
    return (
        f"😔 *На этот раз не повезло*\n\n"
        f"👤 {user_label}, сегодня фортуна прошла мимо.\n\n"
        f"🎲 Шанс был *1 к {BONUS_WIN_ODDS:,}* — звёзды не сошлись.\n\n"
        f"⏰ Новая попытка через *24 часа*. Удачи в следующий раз! 🍀".replace(",", " ")
    )


def format_bonus_win(user_label: str, amount: float) -> str:
    money = _fmt_money(amount)
    return (
        f"🎉🍀 *ДЖЕКПОТ УДАЧИ!* 🍀🎉\n\n"
        f"👤 {user_label}, поздравляем!\n\n"
        f"💰 Вам выпало: *{money}*\n"
        f"🎲 Шанс был *1 к {BONUS_WIN_ODDS:,}* — и вы его поймали!\n\n"
        f"⏰ Следующая попытка через *24 часа*.".replace(",", " ")
    )


def format_bonus_result(user_label: str, result: dict[str, Any]) -> str:
    status = str(result.get("status"))
    if status == "cooldown":
        return format_bonus_cooldown(user_label, int(result["wait_seconds"]))
    if status == "win":
        return format_bonus_win(user_label, float(result["amount"]))
    return format_bonus_lose(user_label)
