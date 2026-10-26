"""Чат: список комнат, переписка, история (JSON), картинки, удаление и WebSocket /ws/chat (§8.6).

Текст и стикеры идут через WebSocket, картинки — POST-загрузкой; после сохранения сервер рассылает событие
всем участникам комнаты, в том числе отправителю (так у него на всех устройствах одинаково).
"""

import asyncio
import json
import logging
import re

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession
from starlette.concurrency import run_in_threadpool
from starlette.websockets import WebSocketState

from app import chat, stamps
from app.auth.deps import require_user
from app.auth.sessions import COOKIE_NAME, get_session_user
from app.chat_hub import Connection, hub
from app.db import SessionLocal, get_db
from app.images import CHAT_UPLOAD_BYTES, InvalidImage, read_limited, save_chat_image
from app.models import ChatMessage, ChatRoom, User
from app.origin import same_site
from app.params import MAX_ID, Id
from app.templating import avatar_url, templates

router = APIRouter()

BAD_IMAGE = "Это не картинка или она повреждена"
_JSON_PATH = re.compile(r"/chat/(\d+/(messages|image)|messages/\d+/delete)")


def is_json_path(path: str) -> bool:
    """Адреса, которые зовёт chat.js: ошибки там — JSON, а не страница (см. main.py)."""
    return _JSON_PATH.fullmatch(path) is not None


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _room_or_404(db: DbSession, user: User, room_id: int) -> ChatRoom:
    room = db.get(ChatRoom, room_id)
    if room is None or not chat.can_access(db, user, room):
        raise HTTPException(404)
    return room


def _broadcast(db: DbSession, room: ChatRoom, make) -> None:
    """make(moderator) -> событие; каждый участник получает свой вариант."""
    variants = {flag: make(flag) for flag in (False, True)}
    hub.send({uid: variants[mod] for uid, mod in chat.participants(db, room).items()})


def _broadcast_message(db: DbSession, room: ChatRoom, message: ChatMessage, client_id: str | None = None) -> None:
    def make(moderator: bool) -> dict:
        event = {"type": "message", "message": chat.serialize(message, moderator)}
        if client_id:
            event["client_id"] = client_id
        return event

    _broadcast(db, room, make)


# ---------- счётчик в меню ----------


def unread_for_nav(user) -> int:
    """Число на вкладке «Чат». Вызывается из шаблона меню; сбой не должен ломать страницу."""
    if user is None or getattr(user, "id", None) is None:
        return 0
    try:
        with SessionLocal() as db:
            fresh = db.get(User, user.id)
            return chat.unread_total(db, fresh) if fresh is not None else 0
    except Exception:
        logging.exception("Чат: не удалось посчитать непрочитанные")
        return 0


templates.env.globals["chat_unread"] = unread_for_nav


# ---------- страницы ----------


@router.get("/chat")
def rooms_page(request: Request, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    rooms = chat.rooms_for(db, user)
    ids = [r.id for r in rooms]
    unread = chat.unread_counts(db, user, ids)
    last = chat.last_messages(db, ids)
    items = []
    for room in rooms:
        message = last.get(room.id)
        items.append({
            "room": room,
            "title": chat.room_title(db, room, user),
            "person": chat.room_avatar_user(db, room, user),
            "preview": chat.preview(message, user),
            "time": chat.serialize(message, False)["time"] if message else "",
            "unread": unread.get(room.id, 0),
            "last_id": message.id if message else 0,
        })
    # комнаты с новыми сообщениями выше; у ученика порядок постоянный: группа, потом учитель
    if user.role != "student":
        items.sort(key=lambda item: -item["last_id"])
    return templates.TemplateResponse(request, "chat/rooms.html", {"user": user, "items": items})


@router.get("/chat/{room_id}")
def room_page(room_id: Id, request: Request, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    room = _room_or_404(db, user, room_id)
    moderator = chat.can_moderate(db, user, room)
    messages = chat.history(db, room)
    if messages:
        chat.mark_read(db, user.id, room.id, messages[-1].id)
    person = chat.room_avatar_user(db, room, user)
    data = {
        "room_id": room.id,
        "kind": room.kind,
        "me": user.id,
        "moderator": moderator,
        "messages": [chat.serialize(m, moderator) for m in messages],
        "has_more": len(messages) == chat.PAGE_SIZE,
        "text_max": chat.TEXT_MAX,
        "swear_reason": stamps.PENALTY_REASON_SWEARING,
        "stickers": [{"id": key, "label": label, "url": f"/static/stickers/{key}.svg"} for key, label in chat.STICKERS.items()],
    }
    # меню не показываем (user в шаблон не передаём): переписка на весь экран, как плеер
    context = {
        "room": room,
        "title": chat.room_title(db, room, user),
        "person": person,
        "person_avatar": avatar_url(person),
        "data": data,
    }
    return templates.TemplateResponse(request, "chat/room.html", context)


@router.get("/chat/{room_id}/messages")
def messages(
    room_id: Id,
    before: int | None = Query(None, ge=1, le=MAX_ID),
    after: int | None = Query(None, ge=0, le=MAX_ID),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    room = _room_or_404(db, user, room_id)
    moderator = chat.can_moderate(db, user, room)
    rows = chat.history(db, room, before=before, after=after)
    return {"messages": [chat.serialize(m, moderator) for m in rows], "has_more": len(rows) == chat.PAGE_SIZE}


@router.post("/chat/{room_id}/image")
def upload_image(
    room_id: Id,
    image: UploadFile = File(...),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    room = _room_or_404(db, user, room_id)
    try:
        chat.check_rate(user)  # до обработки: она дорогая
    except chat.ChatError as exc:
        return _error(429, str(exc))
    try:
        rel = save_chat_image(room.id, read_limited(image.file, CHAT_UPLOAD_BYTES))
    except InvalidImage:
        return _error(400, BAD_IMAGE)
    message = chat.post_image(db, user, room, rel)
    _broadcast_message(db, room, message)
    return {"message": chat.serialize(message, chat.can_moderate(db, user, room))}


@router.post("/chat/messages/{message_id}/delete")
def delete(message_id: Id, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    message = db.get(ChatMessage, message_id)
    room = db.get(ChatRoom, message.room_id) if message is not None else None
    if room is None or not chat.can_access(db, user, room):
        raise HTTPException(404)
    if not chat.delete_message(db, user, message, chat.can_moderate(db, user, room)):
        return _error(403, "Это сообщение удалить нельзя")
    _broadcast(db, room, lambda _: {"type": "deleted", "room_id": room.id, "id": message.id})
    return {"ok": True}


# ---------- WebSocket ----------


def _session_user(db: DbSession, token: str | None) -> User | None:
    """Пользователь подключения; проверяется на каждом сообщении — сессию могли завершить (сброс пароля)."""
    user = get_session_user(db, token)
    return None if user is None or user.must_change_password else user


def _ws_user_id(token: str | None) -> int | None:
    with SessionLocal() as db:
        user = _session_user(db, token)
        return user.id if user is not None else None


class _Closed(Exception):
    """Сессия больше не действует — подключение закрываем."""


def _ws_send(token: str | None, room_id: int, text: str | None, sticker: str | None, client_id: str | None) -> None:
    with SessionLocal() as db:
        user = _session_user(db, token)
        if user is None:
            raise _Closed
        room = db.get(ChatRoom, room_id)
        if room is None or not chat.can_access(db, user, room):
            raise chat.ChatError(chat.NO_ACCESS)
        if sticker is not None:
            message = chat.post_sticker(db, user, room, sticker)
        else:
            message = chat.post_text(db, user, room, text or "")
        _broadcast_message(db, room, message, client_id)


def _ws_read(token: str | None, room_id: int, message_id: int) -> None:
    with SessionLocal() as db:
        user = _session_user(db, token)
        if user is None:
            raise _Closed
        room = db.get(ChatRoom, room_id)
        if room is None or not chat.can_access(db, user, room):
            return
        last = db.scalar(select(func.max(ChatMessage.id)).where(ChatMessage.room_id == room.id)) or 0
        chat.mark_read(db, user.id, room.id, min(message_id, last))


def _int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value <= MAX_ID else None


async def _handle(conn: Connection, token: str | None, raw: str) -> None:
    try:
        data = json.loads(raw)
    except ValueError:
        return
    if not isinstance(data, dict):
        return
    kind, room_id = data.get("type"), _int(data.get("room_id"))
    client_id = data.get("client_id") if isinstance(data.get("client_id"), str) else None
    client_id = client_id[:40] if client_id else None
    if kind == "send" and room_id:
        text, sticker = data.get("text"), data.get("sticker")
        if not isinstance(text, str | None) or not isinstance(sticker, str | None) or len(text or "") > 10 * chat.TEXT_MAX:
            text, sticker = "", None
        try:
            await run_in_threadpool(_ws_send, token, room_id, text, sticker, client_id)
        except chat.ChatError as exc:
            hub.send_one(conn, {"type": "error", "client_id": client_id, "error": str(exc)})
    elif kind == "read" and room_id and _int(data.get("message_id")):
        await run_in_threadpool(_ws_read, token, room_id, data["message_id"])
    elif kind == "ping":
        hub.send_one(conn, {"type": "pong"})


@router.websocket("/ws/chat")
async def websocket_chat(websocket: WebSocket):
    # cookie SameSite=Lax не защищает WebSocket: чужой сайт может открыть его с нашей cookie — сверяем Origin
    if not same_site(websocket.headers.get("origin")):
        await websocket.close(code=1008)
        return
    token = websocket.cookies.get(COOKIE_NAME)
    user_id = await run_in_threadpool(_ws_user_id, token)
    if user_id is None:
        await websocket.close(code=1008)
        return
    await websocket.accept()
    conn = Connection(websocket, user_id)
    hub.add(conn)
    writer = asyncio.create_task(conn.writer())
    try:
        while True:
            await _handle(conn, token, await websocket.receive_text())
    except (WebSocketDisconnect, _Closed, RuntimeError):  # RuntimeError: писатель уже закрыл соединение
        pass
    finally:
        hub.remove(conn)
        writer.cancel()
    if websocket.application_state == WebSocketState.CONNECTED and websocket.client_state == WebSocketState.CONNECTED:
        try:
            await websocket.close(code=1008)  # сессия больше не действует
        except (RuntimeError, WebSocketDisconnect):  # клиент ушёл в тот же момент
            pass
