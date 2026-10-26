"""Общее для учителя и админа при заведении учётных записей: проверки ввода и страница с временным паролем."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import User
from app.templating import templates

NAME_MAX = 50
NAME_REQUIRED = "Введите имя и фамилию"
NAME_TOO_LONG = f"Имя и фамилия — не длиннее {NAME_MAX} символов"
LOGIN_INVALID = "Логин: латинские буквы, цифры, точка, дефис, подчёркивание; от 3 до 32 символов"
LOGIN_TAKEN = "Логин уже занят"


def login_taken(db: DbSession, login: str) -> bool:
    return db.scalar(select(User.id).where(User.login == login)) is not None


def name_errors(first_name: str, last_name: str) -> list[str]:
    if not first_name or not last_name:
        return [NAME_REQUIRED]
    if len(first_name) > NAME_MAX or len(last_name) > NAME_MAX:
        return [NAME_TOO_LONG]
    return []


def temp_password_page(request: Request, user: User, account: User, password: str, created: bool):
    """Пароль показывается только в этом ответе: ни в БД открытым текстом, ни в адресе, ни в кэше."""
    context = {"user": user, "student": account, "password": password, "created": created}
    response = templates.TemplateResponse(request, "teacher/temp_password.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response
