from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from typing import Any


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(raw: str) -> bytes:
    pad = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + pad)


def issue_token(secret: str, payload: dict[str, Any]) -> str:
    body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    sig = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return _b64encode(body) + "." + sig


def verify_token(secret: str, token: str, max_age_sec: int = 60 * 60 * 24 * 7) -> dict[str, Any]:
    try:
        body_b64, sig = token.split(".", 1)
        body = _b64decode(body_b64)
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, sig):
            raise ValueError("bad sig")
        data = json.loads(body.decode("utf-8"))
        exp = int(data.get("exp", 0))
        if int(time.time()) > exp:
            raise ValueError("expired")
        if max_age_sec and (int(time.time()) - int(data.get("iat", 0))) > max_age_sec + 60:
            # soft check; hard expiry is exp
            pass
        return data
    except Exception as e:
        raise ValueError("invalid token") from e


def make_session_payload(telegram_id: int, role: str) -> dict[str, Any]:
    now = int(time.time())
    return {"tg_id": telegram_id, "role": role, "iat": now, "exp": now + 60 * 60 * 24 * 7}
