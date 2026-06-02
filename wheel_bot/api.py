from __future__ import annotations

import asyncio
import json
from typing import Any

import aiosqlite
from aiogram import Bot
from aiogram import Dispatcher
from aiogram.types import Update
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from wheel_bot import db
from wheel_bot.config import Settings
from wheel_bot.session_token import issue_token, make_session_payload, verify_token
from wheel_bot.spin_service import run_wheel_spin, run_wheel_spin_silent, send_silent_announce, send_silent_results
from wheel_bot.tg_validate import validate_webapp_init_data


def _json_body(raw: bytes) -> Any:
    if not raw:
        return None
    return json.loads(raw.decode("utf-8"))


def _role_rank(role: str) -> int:
    return {"user": 0, "admin": 1, "superadmin": 2}.get(role, -1)


async def _auth(request: Request, settings: Settings) -> dict[str, Any]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise PermissionError("missing bearer token")
    token = auth.split(" ", 1)[1].strip()
    return verify_token(settings.session_secret, token)


def create_app(
    settings: Settings,
    conn: aiosqlite.Connection,
    bot: Bot,
    dp: Dispatcher,
    db_lock: asyncio.Lock,
) -> Starlette:
    async def session_route(request: Request) -> Response:
        try:
            body = _json_body(await request.body())
            init_data = str((body or {}).get("initData") or "")
            if not init_data:
                return JSONResponse({"error": "initData required"}, status_code=400)

            validated = validate_webapp_init_data(init_data, settings.bot_token)
            user = validated["user"]
            tg_id = int(user["id"])

            role = await db.get_role(conn, tg_id)
            if role is None:
                await db.ensure_user(conn, tg_id, "user")
                role = "user"

            payload = make_session_payload(tg_id, role)
            token = issue_token(settings.session_secret, payload)
            return JSONResponse({"token": token, "role": role})
        except PermissionError as e:
            return JSONResponse({"error": str(e)}, status_code=401)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def me_route(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            role = await db.get_role(conn, int(payload["tg_id"]))
            return JSONResponse({"telegram_id": int(payload["tg_id"]), "role": role})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=401)

    async def participants_list(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            rows = await db.list_participants(conn)
            return JSONResponse(
                {
                    "participants": [
                        {"id": r.id, "poker_nick": r.poker_nick, "description": r.description, "is_hidden": r.is_hidden}
                        for r in rows
                    ]
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=401)

    async def participants_create(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            nick = str(body.get("poker_nick") or "").strip()
            desc = str(body.get("description") or "").strip()
            if not nick:
                return JSONResponse({"error": "poker_nick required"}, status_code=400)
            pid = await db.insert_participant(conn, nick, desc)
            return JSONResponse({"id": pid})
        except aiosqlite.IntegrityError:
            return JSONResponse({"error": "nick already exists"}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def participants_patch(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            pid = int(request.path_params["id"])
            body = _json_body(await request.body()) or {}
            nick_raw = body.get("poker_nick")
            desc_raw = body.get("description")
            hidden_raw = body.get("is_hidden")
            nick = None if nick_raw is None else str(nick_raw).strip()
            desc = None if desc_raw is None else str(desc_raw).strip()
            is_hidden = None if hidden_raw is None else bool(hidden_raw)
            await db.update_participant(conn, pid, nick, desc, is_hidden)
            return JSONResponse({"ok": True})
        except aiosqlite.IntegrityError:
            return JSONResponse({"error": "nick already exists"}, status_code=409)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def participants_delete(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            pid = int(request.path_params["id"])
            ok = await db.delete_participant(conn, pid)
            if not ok:
                return JSONResponse(
                    {
                        "error": (
                            "Удалить нельзя: этот участник уже фигурирует в истории колёс. "
                            "Чтобы убрать его из списка, используйте «Скрыть»."
                        )
                    },
                    status_code=409,
                )
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def wheel_draft_get(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            draft = await db.get_draft_ids(conn)
            last = await db.get_last_roster_ids(conn)
            selected = draft if draft else last
            rows = await db.list_participants(conn, include_hidden=False)
            visible_ids = {r.id for r in rows}
            selected = [pid for pid in selected if pid in visible_ids]
            last = [pid for pid in last if pid in visible_ids]
            return JSONResponse(
                {
                    "selected_ids": selected,
                    "last_roster_ids": last,
                    "participants": [
                        {"id": r.id, "poker_nick": r.poker_nick, "description": r.description} for r in rows
                    ],
                }
            )
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=401)

    async def wheel_draft_put(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            ids = [int(x) for x in (body.get("selected_ids") or [])]
            visible_rows = await db.list_participants(conn, include_hidden=False)
            visible_ids = {r.id for r in visible_rows}
            ids = [x for x in ids if x in visible_ids]
            await db.set_draft_ids(conn, ids)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def wheel_spin(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            depositor_id = int(body.get("depositor_id"))
            deposit_amount = float(body.get("deposit_amount"))
            prizes = [float(x) for x in (body.get("prizes") or [])]
            selected_ids = [int(x) for x in (body.get("selected_ids") or [])]
            announce_delay_sec = int(body.get("announce_delay_sec", 30))

            async with db_lock:
                result = await run_wheel_spin(
                    conn=conn,
                    bot=bot,
                    chat_id=int(settings.target_chat_id),
                    admin_telegram_id=int(payload["tg_id"]),
                    depositor_id=depositor_id,
                    deposit_amount=deposit_amount,
                    selected_ids=selected_ids,
                    prizes=prizes,
                    announce_delay_sec=announce_delay_sec,
                )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def wheel_preview_send(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            raw_ids = body.get("selected_ids") or []
            selected_ids = [int(x) for x in raw_ids]

            visible_rows = await db.list_participants(conn, include_hidden=False)
            visible_map = {r.id: r for r in visible_rows}
            ordered = [pid for pid in selected_ids if pid in visible_map]
            if not ordered:
                return JSONResponse({"error": "нет участников в текущем колесе"}, status_code=400)

            lines: list[str] = []
            for i, pid in enumerate(ordered, start=1):
                r = visible_map[pid]
                desc = (r.description or "").strip()
                label = f"{r.poker_nick} ({desc})" if desc else str(r.poker_nick)
                lines.append(f"{i}. {label}")

            text = "🎯 Предварительный список на колесо:\n\n" + "\n".join(lines)
            await bot.send_message(int(settings.target_chat_id), text)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def wheel_silent_spin(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            depositor_id = int(body.get("depositor_id"))
            deposit_amount = float(body.get("deposit_amount"))
            prizes = [float(x) for x in (body.get("prizes") or [])]
            selected_ids = [int(x) for x in (body.get("selected_ids") or [])]
            async with db_lock:
                result = await run_wheel_spin_silent(
                    conn=conn,
                    bot=bot,
                    chat_id=int(settings.target_chat_id),
                    admin_telegram_id=int(payload["tg_id"]),
                    depositor_id=depositor_id,
                    deposit_amount=deposit_amount,
                    selected_ids=selected_ids,
                    prizes=prizes,
                )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def wheel_silent_send_results(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            session_id = int(body.get("session_id"))
            async with db_lock:
                result = await send_silent_results(
                    conn=conn, bot=bot, chat_id=int(settings.target_chat_id), session_id=session_id
                )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def wheel_silent_announce(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            depositor_id = int(body.get("depositor_id"))
            deposit_amount = float(body.get("deposit_amount"))
            prizes = [float(x) for x in (body.get("prizes") or [])]
            selected_ids = [int(x) for x in (body.get("selected_ids") or [])]
            async with db_lock:
                result = await send_silent_announce(
                    conn=conn,
                    bot=bot,
                    chat_id=int(settings.target_chat_id),
                    admin_telegram_id=int(payload["tg_id"]),
                    depositor_id=depositor_id,
                    deposit_amount=deposit_amount,
                    selected_ids=selected_ids,
                    prizes=prizes,
                )
            return JSONResponse(result)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=500)

    async def wheel_history(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            rows = await db.list_wheel_history(conn, int(settings.target_chat_id), limit=200)
            return JSONResponse({"items": rows})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def message_templates_get(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            templates = await db.get_message_templates(conn)
            return JSONResponse({"templates": templates})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def message_templates_put(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            templates = body.get("templates") or {}
            await db.set_message_templates(conn, dict(templates))
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def message_templates_reset(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if _role_rank(str(payload.get("role"))) < _role_rank("admin"):
                return JSONResponse({"error": "forbidden"}, status_code=403)
            await db.reset_message_templates(conn)
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def admins_list(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if str(payload.get("role")) != "superadmin":
                return JSONResponse({"error": "forbidden"}, status_code=403)
            rows = await db.list_admins(conn)
            return JSONResponse({"admins": rows})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=401)

    async def admins_create(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if str(payload.get("role")) != "superadmin":
                return JSONResponse({"error": "forbidden"}, status_code=403)
            body = _json_body(await request.body()) or {}
            tg = int(body.get("telegram_id"))
            await db.set_role(conn, tg, "admin")
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def admins_delete(request: Request) -> Response:
        try:
            payload = await _auth(request, settings)
            if str(payload.get("role")) != "superadmin":
                return JSONResponse({"error": "forbidden"}, status_code=403)
            tg = int(request.path_params["id"])
            if tg in settings.superadmin_ids:
                return JSONResponse({"error": "cannot change bootstrap superadmin"}, status_code=409)
            await db.set_role(conn, tg, "user")
            return JSONResponse({"ok": True})
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    async def health(_: Request) -> Response:
        return JSONResponse({"ok": True})

    async def telegram_webhook(request: Request) -> Response:
        secret = request.headers.get("x-telegram-bot-api-secret-token", "")
        if secret != settings.webhook_secret:
            return JSONResponse({"error": "forbidden"}, status_code=403)
        try:
            body = _json_body(await request.body()) or {}
            update = Update.model_validate(body)
            await dp.feed_update(bot, update)
            return Response(status_code=200)
        except Exception as e:
            return JSONResponse({"error": str(e)}, status_code=400)

    routes = [
        Route("/health", health, methods=["GET"]),
        Route("/api/session", session_route, methods=["POST"]),
        Route("/api/me", me_route, methods=["GET"]),
        Route("/api/participants", participants_list, methods=["GET"]),
        Route("/api/participants", participants_create, methods=["POST"]),
        Route("/api/participants/{id}", participants_patch, methods=["PATCH"]),
        Route("/api/participants/{id}", participants_delete, methods=["DELETE"]),
        Route("/api/wheel/draft", wheel_draft_get, methods=["GET"]),
        Route("/api/wheel/draft", wheel_draft_put, methods=["PUT"]),
        Route("/api/wheel/spin", wheel_spin, methods=["POST"]),
        Route("/api/wheel/preview-send", wheel_preview_send, methods=["POST"]),
        Route("/api/wheel/silent-spin", wheel_silent_spin, methods=["POST"]),
        Route("/api/wheel/silent-announce", wheel_silent_announce, methods=["POST"]),
        Route("/api/wheel/silent-send-results", wheel_silent_send_results, methods=["POST"]),
        Route("/api/wheel/history", wheel_history, methods=["GET"]),
        Route("/api/message-templates", message_templates_get, methods=["GET"]),
        Route("/api/message-templates", message_templates_put, methods=["PUT"]),
        Route("/api/message-templates/reset", message_templates_reset, methods=["POST"]),
        Route("/api/admins", admins_list, methods=["GET"]),
        Route("/api/admins", admins_create, methods=["POST"]),
        Route("/api/admins/{id}", admins_delete, methods=["DELETE"]),
        Route(settings.webhook_path, telegram_webhook, methods=["POST"]),
        Mount("/webapp", StaticFiles(directory=str(settings.static_dir / "webapp"), html=True)),
    ]

    app = Starlette(routes=routes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    return app
