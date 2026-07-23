from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

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
            from wheel_bot.destinations import log_effective_destinations, validate_destination_ids
            from wheel_bot.posting import clear_legacy_destination_kv, get_wheel_channel_id

            if await clear_legacy_destination_kv(conn):
                log.info("Removed legacy app_kv chat/channel overrides (IDs now only from .env)")
            validate_destination_ids(
                settings.target_chat_id,
                get_wheel_channel_id(settings),
            )
            await log_effective_destinations(conn, settings)
        except Exception:
            log.exception("Destination log skipped (check wheel_bot/destinations.py)")

        db_lock = asyncio.Lock()
        bot = Bot(settings.bot_token)
        dp = Dispatcher()
        dp.include_router(setup_router(settings, conn, db_lock))

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

        @asynccontextmanager
        async def app_lifespan(_: Any):
            digest_task: asyncio.Task[None] | None = None
            try:
                await bot.set_webhook(
                    url=webhook_url,
                    secret_token=settings.webhook_secret,
                    allowed_updates=allowed_updates,
                )
                log.info("Webhook registered: %s", webhook_url)
            except Exception:
                log.exception("Webhook registration failed")
            try:
                await bot.set_my_commands(
                    [
                        BotCommand(command="mines", description="Мины 5×5"),
                        BotCommand(command="min", description="Мины (коротко)"),
                        BotCommand(command="blackjack", description="Блэкджек"),
                        BotCommand(command="bj", description="Блэкджек (коротко)"),
                        BotCommand(command="dogslot", description="The Dog House"),
                        BotCommand(command="dog", description="Слот (коротко)"),
                        BotCommand(command="games", description="Топ мини-игр"),
                        BotCommand(command="stat", description="Статистика колеса"),
                    ]
                )
                log.info("Bot commands registered (incl. /min alias)")
            except Exception:
                log.exception("Failed to set bot commands")
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
            try:
                from wheel_bot.morning_digest import run_morning_digest_scheduler

                digest_task = asyncio.create_task(
                    run_morning_digest_scheduler(bot, settings, conn, db_lock),
                    name="morning_digest_scheduler",
                )
            except Exception:
                log.exception("Failed to start morning digest scheduler")
            yield
            if digest_task is not None:
                digest_task.cancel()
                try:
                    await digest_task
                except asyncio.CancelledError:
                    pass
            try:
                await bot.delete_webhook(drop_pending_updates=False)
            except Exception:
                log.exception("Webhook delete failed")
            await bot.session.close()
            await conn.close()

        app = create_app(settings, conn, bot, dp, db_lock, lifespan=app_lifespan)
    except Exception:
        log.exception("Failed to initialize database or Telegram app")
        raise

    log.info("Wheel bot HTTP on %s:%s", settings.http_host, settings.http_port)
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

    cfg = uvicorn.Config(**uv_kwargs)
    server = uvicorn.Server(cfg)
    await server.serve()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
