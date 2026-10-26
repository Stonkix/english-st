"""Админ: раздел «Школа» — учителя (создание, временный пароль, сброс, отключение)."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.accounts import LOGIN_INVALID, LOGIN_TAKEN, login_taken, name_errors, temp_password_page
from app.auth.deps import require_role
from app.auth.login_guard import reset_all
from app.auth.passwords import generate_temp_password, hash_password
from app.auth.sessions import revoke_user_sessions
from app.db import get_db
from app.logins import is_valid_login, normalize_login
from app.models import User
from app.params import Id
from app.templating import templates

router = APIRouter(prefix="/school")
admin_only = require_role("admin")


def own_teacher(db: DbSession, teacher_id: int) -> User:
    """Только учитель: ученики и админы здесь не трогаются (404)."""
    teacher = db.get(User, teacher_id)
    if teacher is None or teacher.role != "teacher":
        raise HTTPException(404)
    return teacher


def _teachers_page(request: Request, db: DbSession, user: User, values: dict | None = None, errors: list[str] | None = None):
    stmt = select(User).where(User.role == "teacher").order_by(User.last_name, User.first_name, User.id)
    context = {"user": user, "teachers": list(db.scalars(stmt)), "values": values or {}, "errors": errors or []}
    return templates.TemplateResponse(request, "school/teachers.html", context)


def _to_teachers() -> RedirectResponse:
    return RedirectResponse("/school/teachers", status_code=303)


@router.get("")
def index(request: Request, unlocked: bool = False, user: User = Depends(admin_only)):
    return templates.TemplateResponse(request, "school/index.html", {"user": user, "unlocked": unlocked})


@router.post("/login-blocks/reset")
def reset_login_blocks(user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    """Снимает все блокировки входа разом (например, заблокирован весь класс за одним IP)."""
    reset_all(db)
    return RedirectResponse("/school?unlocked=1", status_code=303)


@router.get("/teachers")
def teachers(request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    return _teachers_page(request, db, user)


@router.post("/teachers")
def create_teacher(
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    login: str = Form(""),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    first_name, last_name, login = first_name.strip(), last_name.strip(), normalize_login(login)
    values = {"first_name": first_name, "last_name": last_name, "login": login}

    errors = name_errors(first_name, last_name, db)
    if not is_valid_login(login):
        errors.append(LOGIN_INVALID)
    elif login_taken(db, login):
        errors.append(LOGIN_TAKEN)
    if errors:
        return _teachers_page(request, db, user, values, errors)

    password = generate_temp_password()
    teacher = User(
        login=login,
        password_hash=hash_password(password),
        role="teacher",
        first_name=first_name,
        last_name=last_name,
        must_change_password=True,
    )
    db.add(teacher)
    try:
        db.commit()
    except IntegrityError:
        # двойное нажатие: параллельный запрос успел занять логин между проверкой и вставкой
        db.rollback()
        return _teachers_page(request, db, user, values, [LOGIN_TAKEN])
    return temp_password_page(request, user, teacher, password, created=True)


@router.post("/teachers/{teacher_id}/reset-password")
def reset_password(teacher_id: Id, request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    teacher = own_teacher(db, teacher_id)
    password = generate_temp_password()
    teacher.password_hash = hash_password(password)
    teacher.must_change_password = True
    revoke_user_sessions(db, teacher.id)  # коммитит вместе с новым хешем
    return temp_password_page(request, user, teacher, password, created=False)


@router.post("/teachers/{teacher_id}/deactivate")
def deactivate_teacher(teacher_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    teacher = own_teacher(db, teacher_id)  # группы остаются за учителем, ученики не затронуты
    teacher.is_active = False
    revoke_user_sessions(db, teacher.id)
    return _to_teachers()


@router.post("/teachers/{teacher_id}/activate")
def activate_teacher(teacher_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    teacher = own_teacher(db, teacher_id)
    teacher.is_active = True
    db.commit()
    return _to_teachers()
