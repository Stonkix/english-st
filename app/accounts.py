"""Общее для учителя и админа при заведении учётных записей: проверки ввода и страница с временным паролем."""

from fastapi import Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import User
from app.profanity import contains_profanity, load_filter
from app.templating import templates

NAME_MAX = 50
NAME_REQUIRED = "Enter a first and last name"
NAME_TOO_LONG = f"First and last names must each be at most {NAME_MAX} characters"
LOGIN_INVALID = "Use 3–32 characters in the username: English letters, numbers, periods, hyphens, or underscores"
LOGIN_TAKEN = "That username is already taken"
NAME_BAD_WORD = "Choose a name without flagged words"


def login_taken(db: DbSession, login: str) -> bool:
    return db.scalar(select(User.id).where(User.login == login)) is not None


def name_errors(first_name: str, last_name: str, db: DbSession | None = None) -> list[str]:
    """С db имена проверяются ещё и фильтром мата (CLI первого админа его не передаёт)."""
    if not first_name or not last_name:
        return [NAME_REQUIRED]
    if len(first_name) > NAME_MAX or len(last_name) > NAME_MAX:
        return [NAME_TOO_LONG]
    if db is not None and contains_profanity(f"{first_name} {last_name}", load_filter(db)):
        return [NAME_BAD_WORD]
    return []


def temp_password_page(request: Request, user: User, account: User, password: str, created: bool):
    """Пароль показывается только в этом ответе: ни в БД открытым текстом, ни в адресе, ни в кэше."""
    context = {"user": user, "student": account, "password": password, "created": created}
    response = templates.TemplateResponse(request, "teacher/temp_password.html", context)
    response.headers["Cache-Control"] = "no-store"
    return response
