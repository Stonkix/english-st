"""Вход, выход и смена пароля. Главная страница — в routes/daily.py."""

import math
import re
from datetime import datetime

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import client_ip, get_current_user, home_for, require_user
from app.auth.login_guard import blocked_until, register_failure, register_success
from app.auth.passwords import check_password, hash_password, new_password_errors
from app.auth.sessions import (
    COOKIE_NAME,
    clear_session_cookie,
    create_session,
    revoke_session,
    revoke_user_sessions,
    set_session_cookie,
)
from app.db import get_db
from app.logins import MAX_LEN as LOGIN_MAX_LEN
from app.logins import is_valid_login, normalize_login
from app.models import User
from app.templating import templates
from app.timeutil import utcnow

router = APIRouter()

WRONG_CREDENTIALS = "Неверный логин или пароль"
# Для несуществующего логина пароль сверяется с этим хешем, чтобы время ответа не выдавало, есть ли такой логин
_DUMMY_HASH = hash_password("dummy-password-for-timing")
_NON_LATIN = re.compile(r"[^\x21-\x7E]")


def _clean(value: str) -> str:
    return value.encode("utf-8", "replace").decode()


def _login_page(request: Request, login: str, error: str | None, hint: bool, status: int = 200):
    context = {"login": login, "error": error, "hint": hint}
    return templates.TemplateResponse(request, "auth/login.html", context, status_code=status)


def _blocked_page(request: Request, login: str, until: datetime):
    minutes = max(1, math.ceil((until - utcnow()).total_seconds() / 60))
    return _login_page(request, login, f"Слишком много попыток. Попробуйте через {minutes} мин.", False, 429)


@router.get("/login")
def login_form(request: Request, user: User | None = Depends(get_current_user)):
    if user:
        return RedirectResponse(home_for(user), status_code=303)
    return _login_page(request, "", None, False)


@router.post("/login")
def login(
    request: Request,
    login: str = Form(""),
    password: str = Form(""),
    db: DbSession = Depends(get_db),
):
    # одинокий суррогат (multipart с charset=utf-7) не кодируется в UTF-8 и роняет запись в БД
    login, password = _clean(login), _clean(password).strip()  # пробел, прихваченный при копировании, — не ошибка
    normalized = normalize_login(login)
    key = normalized[:LOGIN_MAX_LEN]  # ключ блокировки не должен расти вместе со входной строкой
    ip = client_ip(request)

    # Блок проверяем до пароля: заблокированному не даём даже пытаться
    until = blocked_until(db, key, ip)
    if until:
        return _blocked_page(request, login, until)

    user = db.scalar(select(User).where(User.login == normalized)) if is_valid_login(normalized) else None
    if user is not None and user.is_active:
        ok = check_password(password, user.password_hash)
    else:
        check_password(password, _DUMMY_HASH)
        ok = False

    if not ok:
        until = register_failure(db, key, ip)
        if until:
            return _blocked_page(request, login, until)
        return _login_page(request, login, WRONG_CREDENTIALS, bool(_NON_LATIN.search(password)))

    register_success(db, key, ip)
    old_token = request.cookies.get(COOKIE_NAME)
    if old_token:
        revoke_session(db, old_token)
    token = create_session(db, user, ip, request.headers.get("user-agent", "")[:300])
    response = RedirectResponse("/password/new" if user.must_change_password else home_for(user), status_code=303)
    set_session_cookie(response, token)
    return response


@router.post("/logout")
def logout(request: Request, db: DbSession = Depends(get_db)):
    token = request.cookies.get(COOKIE_NAME)
    if token:
        revoke_session(db, token)
    response = RedirectResponse("/login", status_code=303)
    clear_session_cookie(response)
    return response


def _password_page(request: Request, errors: list[str]):
    # Пока пароль временный, идти больше некуда: меню не показываем (user в шаблон не передаём)
    return templates.TemplateResponse(request, "auth/password_new.html", {"errors": errors})


# Этот экран — только для замены временного пароля. Добровольная смена (с текущим паролем) — в профиле.
@router.get("/password/new")
def password_form(request: Request, user: User = Depends(require_user)):
    if not user.must_change_password:
        return RedirectResponse(home_for(user), status_code=303)
    return _password_page(request, [])


@router.post("/password/new")
def password_change(
    request: Request,
    new: str = Form(""),
    repeat: str = Form(""),
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    if not user.must_change_password:
        return RedirectResponse(home_for(user), status_code=303)
    new, repeat = new.strip(), repeat.strip()
    errors = new_password_errors(new, repeat)
    if errors:
        return _password_page(request, errors)

    user.password_hash = hash_password(new)
    user.must_change_password = False
    # временный пароль мог увидеть кто-то ещё и войти с другого устройства: чужие сессии завершаем (одним коммитом)
    revoke_user_sessions(db, user.id, keep_token=request.cookies.get(COOKIE_NAME))
    return RedirectResponse(home_for(user), status_code=303)
