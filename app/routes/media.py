"""Выдача фото: только вошедшим и только тем, кому можно (см. app/auth/access.py).

Публичного mount-а /media нет. В проде файл отдаёт nginx по внутреннему адресу /_protected/…
(X-Accel-Redirect), здесь — только проверка прав.
"""

import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session as DbSession

from app.auth.access import can_view_avatar
from app.auth.deps import require_user
from app.config import settings
from app.db import get_db
from app.models import User

router = APIRouter()

_USER_ID = re.compile(r"[1-9][0-9]{0,8}")  # только ASCII-цифры, без ведущих нулей: «007» — не тот же адрес
_NAME = re.compile(r"[0-9a-f]{12}\.webp")
_HEADERS = {"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"}


@router.get("/media/avatars/{user_id}/{name}")
def avatar(user_id: str, name: str, viewer: User = Depends(require_user), db: DbSession = Depends(get_db)):
    if not _USER_ID.fullmatch(user_id) or not _NAME.fullmatch(name):
        raise HTTPException(404)
    owner = db.get(User, int(user_id))
    # отдаём только текущее фото владельца: старые и удалённые файлы недоступны
    if owner is None or owner.avatar != f"avatars/{user_id}/{name}" or not can_view_avatar(viewer, owner):
        raise HTTPException(404)

    if settings.debug:
        path = settings.media_dir / owner.avatar
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/webp", headers=_HEADERS)
    return Response(
        b"", media_type="image/webp",
        headers={**_HEADERS, "X-Accel-Redirect": f"/_protected/{owner.avatar}"},
    )
