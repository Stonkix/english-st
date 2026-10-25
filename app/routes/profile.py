"""Профиль: фото, смена пароля."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.passwords import hash_password, new_password_errors
from app.auth.sessions import COOKIE_NAME, revoke_user_sessions
from app.db import get_db
from app.images import InvalidImage, delete_media, read_limited, save_avatar
from app.models import User
from app.routes.auth import check_password
from app.templating import templates

router = APIRouter()

BAD_IMAGE = "Не получилось открыть картинку. Попробуйте другое фото."
WRONG_CURRENT = "Текущий пароль указан неверно"
SAME_PASSWORD = "Новый пароль совпадает с текущим"


def _page(request: Request, user: User, **context):
    return templates.TemplateResponse(request, "profile.html", {"user": user, **context})


@router.get("/profile")
def profile(request: Request, user: User = Depends(require_user)):
    return _page(request, user)


@router.post("/profile/password")
def change_password(
    request: Request,
    current: str = Form(""),
    new: str = Form(""),
    repeat: str = Form(""),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    if not check_password(current, user.password_hash):
        return _page(request, user, password_errors=[WRONG_CURRENT])
    errors = new_password_errors(new, repeat)
    if new == current:
        errors.append(SAME_PASSWORD)
    if errors:
        return _page(request, user, password_errors=errors)

    user.password_hash = hash_password(new)
    # чужие сессии завершаем (вдруг пароль узнал кто-то ещё); хеш и отзыв сессий — одним коммитом
    revoke_user_sessions(db, user.id, keep_token=request.cookies.get(COOKIE_NAME))
    return _page(request, user, password_ok=True)


@router.post("/profile/avatar")
def upload_avatar(
    request: Request,
    photo: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    try:
        if photo is None:
            raise InvalidImage("Файл не выбран")
        new_path = save_avatar(user.id, read_limited(photo.file))
    except InvalidImage:
        return _page(request, user, avatar_error=BAD_IMAGE)

    old_path = user.avatar
    user.avatar = new_path
    try:
        db.commit()
    except Exception:
        delete_media(new_path)  # в БД запись не легла — новый файл никому не нужен
        raise
    delete_media(old_path)
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/avatar/delete")
def delete_avatar(
    request: Request,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    old_path = user.avatar
    user.avatar = None
    db.commit()
    delete_media(old_path)
    return RedirectResponse("/profile", status_code=303)
