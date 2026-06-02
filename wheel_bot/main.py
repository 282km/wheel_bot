from __future__ import annotations

import asyncio
import logging
from typing import Any

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import MenuButtonWebApp, WebAppInfo

from wheel_bot.api import create_app
from wheel_bot.bot_app import setup_router
from wheel_bot.config import load_settings
from wheel_bot.db import bootstrap_users, connect


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("wheel_bot")

    try:
        settings = load_settings()
    except Exception:
        log.exception("Failed to load settings (.env)")
        raise

    try:
        conn = await connect(settings.database_path)
        await bootstrap_users(conn, settings.superadmin_ids)

        try:
            from wheel_bot.destinations import log_effective_destinations

            await log_effective_destinations(conn, settings)
        except Exception:
            log.exception("Destination log skipped (check wheel_bot/destinations.py)")

        db_lock = asyncio.Lock()
        bot = Bot(settings.bot_token)
        dp = Dispatcher()
        dp.include_router(setup_router(settings, conn, db_lock))
        app = create_app(settings, conn, bot, dp, db_lock)
    except Exception:
        log.exception("Failed to initialize database or Telegram app")
        raise

    log.info("Wheel bot starting on %s:%s", settings.http_host, settings.http_port)
    log.info("TARGET_CHAT_ID=%s (stats)", settings.target_chat_id)
    log.info("WHEEL_CHANNEL_ID=%s", settings.wheel_channel_id)

    uv_kwargs: dict[str, Any] = {
        "app": app,
        "host": settings.http_host,
        "port": settings.http_port,
        "log_level": "info",
        "proxy_headers": True,
        "forwarded_allow_ips": settings.forwarded_allow_ips,
    }
    if settings.ssl_certfile and settings.ssl_keyfile:
        uv_kwargs["ssl_certfile"] = settings.ssl_certfile
        uv_kwargs["ssl_keyfile"] = settings.ssl_keyfile

    webhook_url = f"{settings.public_base_url.rstrip('/')}{settings.webhook_path}"
    allowed_updates = sorted(
        {
            *dp.resolve_used_update_types(),
            "message",
            "edited_message",
            "callback_query",
            "my_chat_member",
        }
    )
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.webhook_secret,
        allowed_updates=allowed_updates,
    )
    log.info("Webhook allowed_updates: %s", allowed_updates)
    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(
                text="Колесо",
                web_app=WebAppInfo(url=settings.webapp_url),
            )
        )
        log.info("WebApp menu button set: %s", settings.webapp_url)
    except Exception:
        log.exception("Failed to set WebApp menu button (check BotFather domain)")

    cfg = uvicorn.Config(**uv_kwargs)
    server = uvicorn.Server(cfg)
    try:
        await server.serve()
    finally:
        await bot.delete_webhook(drop_pending_updates=False)
        await bot.session.close()
        await conn.close()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
