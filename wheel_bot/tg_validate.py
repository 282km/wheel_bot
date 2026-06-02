from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any
from urllib.parse import parse_qsl


def validate_webapp_init_data(init_data: str, bot_token: str, max_age_sec: int = 86400) -> dict[str, Any]:
    """
    Validates Telegram WebApp initData per https://core.telegram.org/bots/webapps
    Returns parsed fields including 'user' dict with 'id'.
    """
    pairs = dict(parse_qsl(init_data, strict_parsing=True))
    incoming_hash = pairs.pop("hash", None)
    if not incoming_hash:
        raise ValueError("missing hash")

    auth_date_raw = pairs.get("auth_date")
    if auth_date_raw:
        auth_date = int(auth_date_raw)
        if int(time.time()) - auth_date > max_age_sec:
            raise ValueError("auth_date too old")

    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs.keys()))

    secret_key = hmac.new(b"WebAppData", bot_token.encode("utf-8"), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode("utf-8"), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, incoming_hash):
        raise ValueError("bad hash")

    user_raw = pairs.get("user")
    if not user_raw:
        raise ValueError("missing user")
    user = json.loads(user_raw)
    if not isinstance(user, dict) or "id" not in user:
        raise ValueError("bad user")

    out = dict(pairs)
    out["user"] = user
    return out
