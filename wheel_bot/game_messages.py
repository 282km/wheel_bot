from __future__ import annotations

from wheel_bot.user_labels import plain_player_label


def format_games_welcome() -> str:
    return (
        "🃏 *Блэкджек в чате*\n\n"
        "Играйте против дилера — очки идут в топ недели.\n\n"
        "*Команды:*\n"
        "🃏 `/blackjack` или `/bj` — новая партия (своё сообщение с картами)\n"
        "🃏 Hit / ✋ Stand — кнопки под *вашей* доской (чужие не нажимать)\n"
        "🃏 `/hit` · `/stand` — то же, обновляет вашу доску\n"
        "🏆 `/games` — топ-10 за неделю\n"
        "📈 `/games me` — ваша статистика\n"
        "❓ `/games help` — эта инструкция\n\n"
        "*Очки:*\n"
        "• Натуральная 21 = 70\n"
        "• Победа = 40 · Ничья = 15 · Проигрыш / перебор = 5\n\n"
        "Кулдауна нет. Новая `/blackjack` удаляет старые доски; последняя команда `/bj` остаётся в чате.\n"
        "Топ обнуляется каждый понедельник. Утром в 8:00 — сводка лидеров.\n\n"
        "Также: `/stat` — колесо, `/bonus` — бонус раз в сутки 🍀"
    )


def format_leaderboard(data: dict[str, Any], *, viewer_id: Optional[int] = None) -> str:
    period = str(data.get("period_label") or "")
    rows = data.get("top") or []
    summary = data.get("summary") or {}
    lines = [
        f"🏆 *Топ блэкджека — {period}*",
        "",
    ]
    if not rows:
        lines.extend(
            [
                "Пока никто не играл на этой неделе.",
                "",
                "Начните с `/blackjack`!",
            ]
        )
    else:
        medals = ("🥇", "🥈", "🥉")
        for i, row in enumerate(rows[:10], start=1):
            medal = medals[i - 1] if i <= 3 else f"{i}."
            label = plain_player_label(str(row.get("label") or ""))
            total = int(row.get("points") or 0)
            games = int(row.get("games") or 0)
            lines.append(f"{medal} {label} — {total} очков ({games} игр)")
        lines.extend(
            [
                "",
                f"📊 Партий за неделю: {int(summary.get('total_games', 0))}",
                f"👥 Игроков: {int(summary.get('unique_players', 0))}",
                f"🃏 Натуральных 21: {int(summary.get('naturals', 0))}",
                f"🏆 Побед: {int(summary.get('wins', 0))}",
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
    lines.append("Подробнее: `/games me` · Играть: `/blackjack`")
    return "\n".join(lines)


def format_user_stats(data: dict[str, Any]) -> str:
    label = plain_player_label(str(data.get("label") or ""))
    week = data.get("week") or {}
    lines = [
        f"📈 *{label}* — блэкджек",
        "",
        f"📅 {data.get('period_label', 'Эта неделя')}:",
        f"  🃏 Партий: {int(week.get('games', 0))}",
        f"  💎 Очков: {int(week.get('total_points', 0))}",
        f"  🏆 Побед: {int(week.get('wins', 0))}",
        f"  ✨ Натуральных 21: {int(week.get('naturals', 0))}",
        "",
        f"📊 Место в топе: {int(week.get('rank', 0)) or '—'}",
    ]
    best = data.get("best")
    if best:
        lines.extend(["", f"🔥 Лучший результат: {best}"])
    lines.append("")
    lines.append("🏆 Топ чата: `/games`")
    return "\n".join(lines)
