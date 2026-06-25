from __future__ import annotations

import secrets
from typing import Any

from wheel_bot.bonus_service import format_bonus_wait


_COOLDOWN_LINES: tuple[str, ...] = (
    "🍀 Фортуна ушла пить чай. Возвращайтесь позже!",
    "🎰 Автомат удачи уже дёрнули — следующий подход позже.",
    "🧙 Магия бонуса перезаряжается. Чуть терпения!",
    "🕹️ Кнопка удачи на кулдауне. Не ломайте кнопку.",
)

_LOSE_LINES: tuple[str, ...] = (
    "🎲 Кубики покатились не туда, но попытка засчитана.",
    "🍀 Фортуна сделала вид, что не заметила. Бывает.",
    "🧊 Бонус сегодня заморозился. Завтра попробуем растопить.",
    "🎰 Барабаны прокрутились мимо кассы.",
    "🌚 Удача спряталась в тени. В следующий раз вытащим.",
)

_WIN_LINES: tuple[str, ...] = (
    "⚡ Вот это щелчок по носу вероятности!",
    "🍀 Фортуна подмигнула именно вам.",
    "🎰 Барабаны сошлись красиво!",
    "🚀 Бонус прилетел без предупреждения.",
    "👑 Сегодня удача явно на вашей стороне.",
)


def _fmt_money(x: float) -> str:
    s = f"{x:g}"
    return f"${s.replace('.', ',')}"


def format_bonus_cooldown(user_label: str, wait_seconds: int) -> str:
    wait_text = format_bonus_wait(wait_seconds)
    line = secrets.choice(_COOLDOWN_LINES)
    return (
        f"⏳ *Удача на перерыве*\n\n"
        f"👤 {user_label}, вы уже пробовали удачу.\n\n"
        f"🕐 Следующая попытка через *{wait_text}*.\n\n"
        f"{line}"
    )


def format_bonus_lose(user_label: str) -> str:
    line = secrets.choice(_LOSE_LINES)
    return (
        f"😔 *На этот раз не повезло*\n\n"
        f"👤 {user_label}, сегодня фортуна прошла мимо.\n\n"
        f"{line}\n\n"
        f"⏰ Новая попытка через *24 часа*. Удачи в следующий раз! 🍀"
    )


def format_bonus_win(user_label: str, amount: float) -> str:
    money = _fmt_money(amount)
    line = secrets.choice(_WIN_LINES)
    return (
        f"🎉🍀 *ДЖЕКПОТ УДАЧИ!* 🍀🎉\n\n"
        f"👤 {user_label}, поздравляем!\n\n"
        f"💰 Вам выпало: *{money}*\n"
        f"{line}\n\n"
        f"⏰ Следующая попытка через *24 часа*."
    )


def format_bonus_result(user_label: str, result: dict[str, Any]) -> str:
    status = str(result.get("status"))
    if status == "cooldown":
        return format_bonus_cooldown(user_label, int(result["wait_seconds"]))
    if status == "win":
        return format_bonus_win(user_label, float(result["amount"]))
    return format_bonus_lose(user_label)
