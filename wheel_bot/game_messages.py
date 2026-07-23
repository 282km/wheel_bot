from __future__ import annotations

from typing import Any, Optional

from wheel_bot.game_service import format_wait


def format_slots_result(
    user_label: str,
    *,
    value: int,
    points: int,
    flair: str,
    rank: Optional[int],
    wait_seconds: int = 0,
) -> str:
    lines = [
        f"🎰 {user_label}",
        flair,
        f"💎 +{points} очков",
    ]
    if rank is not None:
        lines.append(f"🏆 Место в недельном топе: {rank}-е")
    if wait_seconds > 0:
        lines.append(f"⏳ Следующий спин через {format_wait(wait_seconds)}")
    return "\n".join(lines)


def format_bowling_result(
    user_label: str,
    *,
    value: int,
    points: int,
    flair: str,
    rank: Optional[int],
    wait_seconds: int = 0,
) -> str:
    lines = [
        f"🎳 {user_label} — сбил {value}",
        flair,
        f"💎 +{points} очков",
    ]
    if rank is not None:
        lines.append(f"🏆 Место в недельном топе: {rank}-е")
    if wait_seconds > 0:
        lines.append(f"⏳ Следующий бросок через {format_wait(wait_seconds)}")
    return "\n".join(lines)


def format_duel_result(
    *,
    challenger_label: str,
    opponent_label: str,
    challenger_value: int,
    opponent_value: int,
    challenger_points: int,
    opponent_points: int,
    winner: str,
) -> str:
    lines = [
        "⚔️ Дуэль!",
        f"👤 {challenger_label}: 🎲 {challenger_value} (+{challenger_points})",
        f"👤 {opponent_label}: 🎲 {opponent_value} (+{opponent_points})",
    ]
    if winner == "tie":
        lines.append("🤝 Ничья — банк делим пополам!")
    elif winner == "challenger":
        lines.append(f"🏆 {challenger_label} забирает банк удачи!")
    else:
        lines.append(f"🏆 {opponent_label} забирает банк удачи!")
    lines.append("")
    lines.append("📊 Топ недели: /games")
    return "\n".join(lines)


def format_games_welcome() -> str:
    return (
        "🎮 *Мини-игры в чате*\n\n"
        "Быстрая удача без денег — только очки и топ недели.\n\n"
        "*Команды:*\n"
        "🎰 `/slots` — слот-машина (кулдаун 10 мин)\n"
        "🎳 `/bowling` — боулинг (кулдаун 10 мин)\n"
        "⚔️ `/duel` — ответьте на сообщение с 🎲 соперника\n"
        "🏆 `/games` — топ-10 за неделю\n"
        "📈 `/games me` — ваша статистика\n"
        "❓ `/games help` — эта инструкция\n\n"
        "*Очки:*\n"
        "• Слоты: джекпот до 100, обычный спин 10–40\n"
        "• Боулинг: страйк (6) = 60, остальное 5–45\n"
        "• Дуэль: победа +25, поражение +5, ничья +12\n\n"
        "Топ обнуляется каждый понедельник. Утром в 8:00 — сводка лидеров.\n\n"
        "Также: `/stat` — колесо, `/bonus` — бонус раз в сутки 🍀"
    )


def format_leaderboard(data: dict[str, Any], *, viewer_id: Optional[int] = None) -> str:
    period = str(data.get("period_label") or "")
    rows = data.get("top") or []
    summary = data.get("summary") or {}
    lines = [
        f"🏆 *Топ мини-игр — {period}*",
        "",
        "🎰 Слоты · 🎳 Боулинг · ⚔️ Дуэли",
        "",
    ]
    if not rows:
        lines.extend(
            [
                "Пока никто не играл на этой неделе.",
                "",
                "Начните с `/slots` или `/bowling`!",
            ]
        )
    else:
        medals = ("🥇", "🥈", "🥉")
        for i, row in enumerate(rows[:10], start=1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            label = str(row.get("label") or "Игрок")
            total = int(row.get("points") or 0)
            games = int(row.get("games") or 0)
            lines.append(f"{medal} {label} — {total} очков ({games} игр)")
        lines.extend(
            [
                "",
                f"📊 Игр за неделю: {int(summary.get('total_games', 0))}",
                f"👥 Игроков: {int(summary.get('unique_players', 0))}",
                f"🎰 Джекпотов: {int(summary.get('jackpots', 0))}",
                f"🎳 Страйков: {int(summary.get('strikes', 0))}",
                f"⚔️ Дуэлей: {int(summary.get('duels', 0))}",
            ]
        )

    viewer = data.get("viewer")
    if viewer_id is not None and viewer:
        lines.extend(
            [
                "",
                f"📈 Вы: {int(viewer.get('points', 0))} очков, "
                f"место {int(viewer.get('rank', 0)) or '—'}",
            ]
        )
    lines.append("")
    lines.append("Подробнее: `/games me` · Играть: `/slots` `/bowling`")
    return "\n".join(lines)


def format_user_stats(data: dict[str, Any]) -> str:
    label = str(data.get("label") or "Игрок")
    week = data.get("week") or {}
    lines = [
        f"📈 *{label}* — мини-игры",
        "",
        f"📅 {data.get('period_label', 'Эта неделя')}:",
        f"  🎰 Слоты: {int(week.get('slots_games', 0))} игр, "
        f"{int(week.get('slots_points', 0))} очков, "
        f"джекпотов {int(week.get('jackpots', 0))}",
        f"  🎳 Боулинг: {int(week.get('bowling_games', 0))} игр, "
        f"{int(week.get('bowling_points', 0))} очков, "
        f"страйков {int(week.get('strikes', 0))}",
        f"  ⚔️ Дуэли: {int(week.get('duel_wins', 0))} побед / "
        f"{int(week.get('duel_losses', 0))} поражений / "
        f"{int(week.get('duel_ties', 0))} ничьих",
        "",
        f"💎 Итого: *{int(week.get('total_points', 0))}* очков "
        f"(место {int(week.get('rank', 0)) or '—'})",
    ]
    best = data.get("best")
    if best:
        lines.extend(["", f"🔥 Лучший результат: {best}"])
    lines.append("")
    lines.append("🏆 Топ чата: `/games`")
    return "\n".join(lines)
