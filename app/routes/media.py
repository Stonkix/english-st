"""Выдача фото: только вошедшим и только тем, кому можно (см. app/auth/access.py).

Публичного mount-а /media нет. В проде файл отдаёт nginx по внутреннему адресу /_protected/…
(X-Accel-Redirect), здесь — только проверка прав.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.access import can_view_avatar, can_view_previous_avatar, can_view_question_image
from app.auth.deps import require_user
from app.config import settings
from app.db import get_db
from app.chat import can_access
from app.models import AvatarHistory, ChatMessage, ChatRoom, Question, User
from app.questions import picture_paths

router = APIRouter()

_USER_ID = re.compile(r"[1-9][0-9]{0,8}")  # только ASCII-цифры, без ведущих нулей: «007» — не тот же адрес
_NAME = re.compile(r"[0-9a-f]{12}\.webp")
_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


def _file_response(rel: str):
    if settings.debug:
        path = settings.media_dir / rel
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers=_HEADERS)
    return Response(
        b"", media_type="image/webp",
        headers={**_HEADERS, "X-Accel-Redirect": f"/_protected/{rel}"},
    )


@router.get("/media/avatars/{user_id}/{name}")
def avatar(user_id: str, name: str, viewer: User = Depends(require_user), db: DbSession = Depends(get_db)):
    if not _USER_ID.fullmatch(user_id) or not _NAME.fullmatch(name):
        raise HTTPException(404)
    owner = db.get(User, int(user_id))
    rel = f"avatars/{user_id}/{name}"
    if owner is None:
        raise HTTPException(404)
    # отдаём текущее фото владельца либо (только учителю и админу) одно из прежних; удалённые файлы недоступны
    is_current = owner.avatar == rel and can_view_avatar(viewer, owner)
    is_previous = (
        not is_current
        and can_view_previous_avatar(viewer, owner)
        and db.scalar(select(AvatarHistory.id).where(AvatarHistory.user_id == owner.id, AvatarHistory.path == rel))
        is not None
    )
    if not (is_current or is_previous):
        raise HTTPException(404)

    return _file_response(rel)


@router.get("/media/questions/{owner_id}/{name}")
def question_image(owner_id: str, name: str, viewer: User = Depends(require_user), db: DbSession = Depends(get_db)):
    if not _USER_ID.fullmatch(owner_id) or not _NAME.fullmatch(name):
        raise HTTPException(404)
    rel = f"questions/{owner_id}/{name}"
    if not can_view_question_image(viewer, int(owner_id), db):
        raise HTTPException(404)
    # отдаём только картинки, на которые ещё ссылается вопрос автора (скрытые тоже: они нужны истории ответов)
    questions = db.scalars(select(Question).where(Question.owner_id == int(owner_id)))
    if not any(rel in picture_paths(q) for q in questions):
        raise HTTPException(404)
    return _file_response(rel)


@router.get("/media/chat/{room_id}/{name}")
def chat_image(room_id: str, name: str, viewer: User = Depends(require_user), db: DbSession = Depends(get_db)):
    """Картинка чата — только участникам комнаты и только пока сообщение не удалено (при удалении путь стирается)."""
    if not _USER_ID.fullmatch(room_id) or not _NAME.fullmatch(name):
        raise HTTPException(404)
    room = db.get(ChatRoom, int(room_id))
    rel = f"chat/{room_id}/{name}"
    if room is None or not can_access(db, viewer, room):
        raise HTTPException(404)
    if db.scalar(select(ChatMessage.id).where(ChatMessage.room_id == room.id, ChatMessage.image == rel)) is None:
        raise HTTPException(404)
    return _file_response(rel)
