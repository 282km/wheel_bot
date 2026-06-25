from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError

if TYPE_CHECKING:
    from wheel_bot.config import Settings


async def notify_superadmins(
    bot: Bot,
    settings: Settings,
    text: str,
    *,
    log: logging.Logger | None = None,
    parse_mode: str | None = None,
) -> None:
    """Личное сообщение всем superadmin из SUPERADMIN_IDS."""
    if not settings.superadmin_ids:
        return
    logger = log or logging.getLogger("wheel_bot.notify")
    for admin_id in settings.superadmin_ids:
        try:
            await bot.send_message(int(admin_id), text, parse_mode=parse_mode)
        except TelegramBadRequest:
            if parse_mode:
                try:
                    await bot.send_message(int(admin_id), text.replace("*", "").replace("`", ""))
                except Exception:
                    logger.exception("superadmin notify fallback failed for %s", admin_id)
            else:
                logger.warning("superadmin notify bad request for %s", admin_id)
        except TelegramForbiddenError:
            logger.warning(
                "superadmin notify: cannot DM %s — напишите боту /start в личке",
                admin_id,
            )
        except Exception:
            logger.exception("superadmin notify failed for %s", admin_id)
