"""Учитель: группы, ученики, временные пароли, сброс, разблокировка и отключение."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.accounts import (
    LOGIN_INVALID,
    LOGIN_TAKEN,
    NAME_MAX,
    login_taken,
    name_errors,
    temp_password_page,
)
from app.avatars import (
    LOCK_PERIODS,
    avatar_locked,
    history,
    lock_message,
    read_photo,
    remove_history_item,
    replace_avatar,
    set_avatar_lock,
    teacher_remove_avatar,
)
from app.auth.deps import require_role
from app.auth.login_guard import is_locked, unlock
from app.auth.passwords import generate_temp_password, hash_password
from app.auth.sessions import revoke_user_sessions
from app.db import get_db
from app.images import InvalidImage
from app.logins import is_valid_login, normalize_login, suggest_login, unique_login
from app.models import Group, User
from app.templating import templates

router = APIRouter(prefix="/teacher")
teacher_only = require_role("teacher")  # админ проходит тоже

GROUP_NAME_MAX = 50
GROUP_NAME_REQUIRED = "Введите название группы"
GROUP_NAME_TOO_LONG = f"Название группы — не длиннее {GROUP_NAME_MAX} символов"
BAD_IMAGE = "Не получилось открыть картинку. Попробуйте другое фото."


# ---------- доступ ----------


def own_group(db: DbSession, user: User, group_id: int) -> Group:
    """Группа учителя (админу — любая); чужая или несуществующая — 404, чтобы не выдавать, что она есть."""
    group = db.get(Group, group_id)
    if group is None or (user.role != "admin" and group.teacher_id != user.id):
        raise HTTPException(404)
    return group


def own_student(db: DbSession, user: User, student_id: int) -> User:
    """Ученик из группы учителя (админу — любой ученик). Учителя и админы сюда не попадают."""
    student = db.get(User, student_id)
    if student is None or student.role != "student":
        raise HTTPException(404)
    if user.role != "admin" and (student.group is None or student.group.teacher_id != user.id):
        raise HTTPException(404)
    return student


def _groups_of(db: DbSession, user: User) -> list[Group]:
    stmt = select(Group).order_by(Group.name, Group.id)
    if user.role != "admin":
        stmt = stmt.where(Group.teacher_id == user.id)
    return list(db.scalars(stmt))


# ---------- проверка введённого ----------


def _group_name_error(name: str) -> str | None:
    if not name:
        return GROUP_NAME_REQUIRED
    if len(name) > GROUP_NAME_MAX:
        return GROUP_NAME_TOO_LONG
    return None


def _group_id_or_404(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise HTTPException(404) from None


# ---------- страницы ----------


def _groups_page(request: Request, db: DbSession, user: User, name: str = "", error: str | None = None):
    counted = select(User.group_id, func.count()).where(User.role == "student").group_by(User.group_id)
    counts = dict(db.execute(counted).all())
    context = {"user": user, "groups": _groups_of(db, user), "counts": counts, "name": name, "error": error}
    return templates.TemplateResponse(request, "teacher/groups.html", context)


def _group_page(request: Request, db: DbSession, user: User, group: Group, **context):
    students = sorted(
        (s for s in group.students if s.role == "student"), key=lambda s: (s.last_name.lower(), s.first_name.lower(), s.id)
    )
    flags = {s.id: {"locked": is_locked(db, s.login)} for s in students}
    context.update(user=user, group=group, students=students, flags=flags)
    return templates.TemplateResponse(request, "teacher/group.html", context)


def _student_form(request: Request, user: User, group: Group, values: dict, errors: list[str]):
    context = {"user": user, "group": group, "values": values, "errors": errors}
    return templates.TemplateResponse(request, "teacher/student_form.html", context)


def _student_page(request: Request, db: DbSession, user: User, student: User, **context):
    context.update(
        user=user,
        student=student,
        groups=_groups_of(db, user),
        locked=is_locked(db, student.login),
        photo_history=history(db, student),
        photo_lock=lock_message(student),
        photo_locked=avatar_locked(student),
    )
    return templates.TemplateResponse(request, "teacher/student.html", context)


def _to_student(student: User) -> RedirectResponse:
    return RedirectResponse(f"/teacher/students/{student.id}", status_code=303)


# ---------- группы ----------


@router.get("")
def groups(request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _groups_page(request, db, user)


@router.post("/groups")
def create_group(
    request: Request,
    name: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    name = name.strip()
    error = _group_name_error(name)
    if error:
        return _groups_page(request, db, user, name=name, error=error)
    group = Group(name=name, teacher_id=user.id)
    db.add(group)
    db.commit()
    return RedirectResponse(f"/teacher/groups/{group.id}", status_code=303)


@router.get("/groups/{group_id}")
def group_page(group_id: int, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _group_page(request, db, user, own_group(db, user, group_id))


@router.post("/groups/{group_id}/rename")
def rename_group(
    group_id: int,
    request: Request,
    name: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    name = name.strip()
    error = _group_name_error(name)
    if error:
        return _group_page(request, db, user, group, rename_error=error, rename_value=name)
    group.name = name
    db.commit()
    return RedirectResponse(f"/teacher/groups/{group.id}", status_code=303)


# ---------- новый ученик ----------


@router.get("/login-suggest")
def login_suggest(
    request: Request,
    first_name: str = Query(""),
    last_name: str = Query(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    """HTMX-фрагмент: поле логина с подставленным значением (или пустое, если подходящего нет)."""
    login = unique_login(db, suggest_login(first_name[:NAME_MAX], last_name[:NAME_MAX]))
    if not is_valid_login(login):
        login = ""
    return templates.TemplateResponse(request, "partials/login_suggest.html", {"login": login})


@router.get("/groups/{group_id}/students/new")
def new_student_form(
    group_id: int, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    return _student_form(request, user, own_group(db, user, group_id), {}, [])


@router.post("/groups/{group_id}/students")
def create_student(
    group_id: int,
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    login: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    first_name, last_name, login = first_name.strip(), last_name.strip(), normalize_login(login)
    values = {"first_name": first_name, "last_name": last_name, "login": login}

    errors = name_errors(first_name, last_name)
    if not is_valid_login(login):
        errors.append(LOGIN_INVALID)
    elif login_taken(db, login):
        errors.append(LOGIN_TAKEN)
    if errors:
        return _student_form(request, user, group, values, errors)

    password = generate_temp_password()
    student = User(
        login=login,
        password_hash=hash_password(password),
        role="student",
        first_name=first_name,
        last_name=last_name,
        group_id=group.id,
        must_change_password=True,
    )
    db.add(student)
    try:
        db.commit()
    except IntegrityError:
        # двойное нажатие: параллельный запрос успел занять логин между проверкой и вставкой
        db.rollback()
        return _student_form(request, user, group, values, [LOGIN_TAKEN])
    return temp_password_page(request, user, student, password, created=True)


# ---------- карточка ученика ----------


@router.get("/students/{student_id}")
def student_page(student_id: int, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _student_page(request, db, user, own_student(db, user, student_id))


@router.post("/students/{student_id}/edit")
def edit_student(
    student_id: int,
    request: Request,
    first_name: str = Form(""),
    last_name: str = Form(""),
    group_id: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    student = own_student(db, user, student_id)
    new_group = own_group(db, user, _group_id_or_404(group_id))  # перенос — только в группы этого учителя
    first_name, last_name = first_name.strip(), last_name.strip()
    errors = name_errors(first_name, last_name)
    if errors:
        return _student_page(request, db, user, student, errors=errors)
    student.first_name, student.last_name, student.group_id = first_name, last_name, new_group.id
    db.commit()
    return _student_page(request, db, user, student, saved=True)


@router.post("/students/{student_id}/reset-password")
def reset_password(
    student_id: int, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    password = generate_temp_password()
    student.password_hash = hash_password(password)
    student.must_change_password = True
    revoke_user_sessions(db, student.id)  # коммитит вместе с новым хешем: старый пароль и сессии умирают разом
    return temp_password_page(request, user, student, password, created=False)


@router.post("/students/{student_id}/unlock")
def unlock_student(student_id: int, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    unlock(db, student.login)
    return _to_student(student)


@router.post("/students/{student_id}/deactivate")
def deactivate_student(student_id: int, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    student.is_active = False
    revoke_user_sessions(db, student.id)  # коммитит и флаг, и удаление сессий
    return _to_student(student)


@router.post("/students/{student_id}/activate")
def activate_student(student_id: int, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    student.is_active = True
    db.commit()
    return _to_student(student)


# ---------- фото ученика (модерация) ----------


@router.post("/students/{student_id}/avatar")
def upload_student_avatar(
    student_id: int,
    request: Request,
    photo: UploadFile | None = File(None),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    student = own_student(db, user, student_id)
    try:
        replace_avatar(db, student, read_photo(photo))  # запрет смены учителя не касается
    except InvalidImage:
        return _student_page(request, db, user, student, avatar_error=BAD_IMAGE)
    return _to_student(student)


@router.post("/students/{student_id}/avatar/delete")
def delete_student_avatar(student_id: int, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    teacher_remove_avatar(db, student)
    return _to_student(student)


@router.post("/students/{student_id}/avatar/history/{item_id}/delete")
def delete_student_avatar_history(
    student_id: int, item_id: int, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    try:
        remove_history_item(db, student, item_id)
    except ValueError:
        raise HTTPException(404) from None
    return _to_student(student)


@router.post("/students/{student_id}/avatar/lock")
def lock_student_avatar(
    student_id: int, period: str = Form("off"), user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    if period != "off" and period not in LOCK_PERIODS:
        raise HTTPException(422, "Неизвестный срок")
    set_avatar_lock(db, student, period)
    return _to_student(student)
