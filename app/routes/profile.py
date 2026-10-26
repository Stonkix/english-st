"""Профиль: фото, смена пароля."""

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.auth.passwords import check_password, hash_password, new_password_errors
from app.auth.sessions import COOKIE_NAME, revoke_user_sessions
from app.db import get_db
from app.avatars import BAD_IMAGE, avatar_locked, lock_message, read_photo, replace_avatar, student_remove_avatar
from app.images import InvalidImage
from app.models import User
from app.templating import templates

router = APIRouter()

WRONG_CURRENT = "Текущий пароль указан неверно"
SAME_PASSWORD = "Новый пароль совпадает с текущим"


def _page(request: Request, user: User, status: int = 200, **context):
    lock = lock_message(user) if user.role == "student" else None  # запрет касается только учеников
    return templates.TemplateResponse(request, "profile.html", {"user": user, "avatar_lock": lock, **context}, status_code=status)


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
    new, repeat = new.strip(), repeat.strip()
    errors = new_password_errors(new, repeat)
    if new == current.strip():
        errors.append(SAME_PASSWORD)
    if errors:
        return _page(request, user, password_errors=errors)

    user.password_hash = hash_password(new)
    # чужие сессии завершаем (вдруг пароль узнал кто-то ещё); хеш и отзыв сессий — одним коммитом
    revoke_user_sessions(db, user.id, keep_token=request.cookies.get(COOKIE_NAME))
    return _page(request, user, password_ok=True)


def _is_locked(user: User) -> bool:
    """Запрет учителя действует только на ученика; проверка на сервере, не только скрытые формы."""
    return user.role == "student" and avatar_locked(user)


@router.post("/profile/avatar")
def upload_avatar(
    request: Request,
    photo: UploadFile | None = File(None),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    if _is_locked(user):  # страница была открыта до запрета: показываем профиль с причиной, а не голую ошибку
        return _page(request, user, 403)
    try:
        replace_avatar(db, user, read_photo(photo))
    except InvalidImage:
        return _page(request, user, avatar_error=BAD_IMAGE)
    return RedirectResponse("/profile", status_code=303)


@router.post("/profile/avatar/delete")
def delete_avatar(request: Request, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    if _is_locked(user):
        return _page(request, user, 403)
    student_remove_avatar(db, user)  # прежнее фото сохраняется для учителя
    return RedirectResponse("/profile", status_code=303)
