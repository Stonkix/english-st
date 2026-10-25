"""Зависимости FastAPI: текущий пользователь и проверка ролей."""

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session as DbSession

from app.auth.sessions import COOKIE_NAME, get_session_user
from app.db import get_db
from app.models import User

# Пока пароль временный, пускаем только на смену пароля и выход
PASSWORD_CHANGE_ALLOWED = {"/password/new", "/logout"}


class LoginRequired(Exception):
    """Нужен вход: обработчик в main.py отправляет на /login."""


class PasswordChangeRequired(Exception):
    """Нужно сменить временный пароль: обработчик в main.py отправляет на /password/new."""


def client_ip(request: Request) -> str:
    # За nginx адрес подставляет uvicorn --proxy-headers
    return request.client.host if request.client else "unknown"


def get_current_user(request: Request, db: DbSession = Depends(get_db)) -> User | None:
    user = get_session_user(db, request.cookies.get(COOKIE_NAME))
    request.state.user = user
    return user


def require_user(request: Request, user: User | None = Depends(get_current_user)) -> User:
    if user is None:
        raise LoginRequired
    if user.must_change_password and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise PasswordChangeRequired
    return user


def require_role(*roles: str):
    """Фабрика зависимости. Админу доступно всё, что доступно учителю."""
    allowed = set(roles)
    if "teacher" in allowed:
        allowed.add("admin")

    def check(user: User = Depends(require_user)) -> User:
        if user.role not in allowed:
            raise HTTPException(403)
        return user

    return check


def home_for(user: User) -> str:
    return "/" if user.role == "student" else "/teacher"
