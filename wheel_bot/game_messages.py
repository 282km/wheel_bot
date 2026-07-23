from __future__ import annotations

from typing import Any, Optional

from wheel_bot.user_labels import plain_player_label


def format_games_welcome() -> str:
    return (
        "🎮 *Мини-игры в чате*\n\n"
        "Блэкджек и «Мины» — очки суммируются в общий топ недели.\n\n"
        "*Блэкджек:*\n"
        "🃏 `/blackjack` или `/bj` — новая партия\n"
        "🃏 Hit / ✋ Stand — кнопки под *вашей* доской\n"
        "🃏 `/hit` · `/stand` — то же текстом\n\n"
        "*Мины (5×5):*\n"
        "💣 `/mines` или `/min` — новая игра (3 мины)\n"
        "💣 `/mines 5` · `/mines 10` — больше мин\n"
        "⬜ Открывайте клетки · 💰 «Забрать» — кэшаут\n"
        "💣 `/cash` — забрать выигрыш текстом\n\n"
        "*Общее:*\n"
        "🏆 `/games` — топ-10 за неделю\n"
        "📈 `/games me` — ваша статистика\n"
        "❓ `/games help` — эта инструкция\n\n"
        "*Очки блэкджек:* натуральная 21 = 70 · победа = 40 · ничья = 15 · проигрыш = 5\n"
        "*Очки мины:* кэшаут ×множитель (до 150) · мина = 5\n\n"
        "Кулдауна нет. Новая игра удаляет старые доски; последняя команда остаётся в чате.\n"
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
    ]
    if not rows:
        lines.extend(
            [
                "Пока никто не играл на этой неделе.",
                "",
                "Начните с `/blackjack` или `/mines`!",
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
                f"🏆 Побед BJ: {int(summary.get('bj_wins', 0))}",
                f"💰 Кэшаутов мин: {int(summary.get('mines_wins', 0))}",
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
    lines.append("Подробнее: `/games me` · 🃏 `/blackjack` · 💣 `/mines`")
    return "\n".join(lines)


def format_user_stats(data: dict[str, Any]) -> str:
    label = plain_player_label(str(data.get("label") or ""))
    week = data.get("week") or {}
    lines = [
        f"📈 *{label}* — мини-игры",
        "",
        f"📅 {data.get('period_label', 'Эта неделя')}:",
        f"  🎮 Всего игр: {int(week.get('games', 0))}",
        f"  💎 Очков: {int(week.get('total_points', 0))}",
        "",
        f"  🃏 Блэкджек: {int(week.get('bj_games', 0))} игр, {int(week.get('bj_points', 0))} очков",
        f"  🏆 Побед BJ: {int(week.get('bj_wins', 0))}",
        f"  ✨ Натуральных 21: {int(week.get('naturals', 0))}",
        "",
        f"  💣 Мины: {int(week.get('mines_games', 0))} игр, {int(week.get('mines_points', 0))} очков",
        f"  💰 Удачных кэшаутов: {int(week.get('mines_cashouts', 0))}",
        "",
        f"📊 Место в топе: {int(week.get('rank', 0)) or '—'}",
    ]
    best = data.get("best") or []
    if best:
        lines.extend(["", "🔥 Лучшие результаты:"])
        for item in best:
            lines.append(f"  • {item}")
    lines.append("")
    lines.append("🏆 Топ чата: `/games`")
    return "\n".join(lines)
