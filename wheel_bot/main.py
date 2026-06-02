from __future__ import annotations

import asyncio
import logging
from typing import Any

import uvicorn
from aiogram import Bot, Dispatcher

from wheel_bot.api import create_app
from wheel_bot.bot_app import setup_router
from wheel_bot.config import load_settings
from wheel_bot.db import bootstrap_users, connect


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    settings = load_settings()
    conn = await connect(settings.database_path)
    await bootstrap_users(conn, settings.superadmin_ids)

    bot = Bot(settings.bot_token)
    dp = Dispatcher()
    dp.include_router(setup_router(settings, conn))

    db_lock = asyncio.Lock()
    app = create_app(settings, conn, bot, dp, db_lock)

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
    await bot.set_webhook(
        url=webhook_url,
        secret_token=settings.webhook_secret,
        allowed_updates=dp.resolve_used_update_types(),
    )

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
