from __future__ import annotations

import logging

import aiohttp

from wheel_bot.config import Settings

log = logging.getLogger("wheel_bot.live")


async def is_stream_live(settings: Settings) -> bool:
    if not settings.live_stream_enabled:
        return False

    api = settings.live_mediamtx_api
    path = settings.live_stream_path
    if api:
        url = f"{api.rstrip('/')}/v3/paths/get/{path}"
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url) as resp:
                    if resp.status == 404:
                        return False
                    if resp.status != 200:
                        log.warning("MediaMTX API %s for path %s", resp.status, path)
                        return False
                    data = await resp.json()
                    return bool(data.get("ready"))
        except Exception:
            log.exception("MediaMTX API check failed for %s", path)

    hls_url = settings.live_hls_url_effective
    try:
        timeout = aiohttp.ClientTimeout(total=3)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.head(hls_url, allow_redirects=True) as resp:
                return resp.status == 200
    except Exception:
        log.exception("HLS availability check failed: %s", hls_url)
        return False
