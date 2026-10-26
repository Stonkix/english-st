"""Учитель: группы, ученики, временные пароли, сброс, разблокировка и отключение."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import PlainTextResponse, RedirectResponse
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
    BAD_IMAGE,
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
from app import stamps
from app.db import get_db
from app.images import InvalidImage
from app.logins import is_valid_login, normalize_login, suggest_login, unique_login
from app.models import Group, User
from app.params import MAX_ID, Id, form_int
from app.results import done_today, group_students
from app.templating import templates
from app.timeutil import moscow_today

router = APIRouter(prefix="/teacher")
teacher_only = require_role("teacher")  # админ проходит тоже

GROUP_NAME_MAX = 50
GROUP_NAME_REQUIRED = "Введите название группы"
GROUP_NAME_TOO_LONG = f"Название группы — не длиннее {GROUP_NAME_MAX} символов"


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
        value = int(raw)
    except ValueError:
        raise HTTPException(404) from None
    if not 1 <= value <= MAX_ID:  # огромное число SQLite не переварит
        raise HTTPException(404)
    return value


# ---------- страницы ----------


def _groups_page(request: Request, db: DbSession, user: User, name: str = "", error: str | None = None):
    counted = select(User.group_id, func.count()).where(User.role == "student").group_by(User.group_id)
    counts = dict(db.execute(counted).all())
    context = {"user": user, "groups": _groups_of(db, user), "counts": counts, "name": name, "error": error}
    return templates.TemplateResponse(request, "teacher/groups.html", context)


def _group_page(request: Request, db: DbSession, user: User, group: Group, **context):
    students = group_students(group)
    flags = {s.id: {"locked": is_locked(db, s.login)} for s in students}
    context.update(
        user=user, group=group, students=students, flags=flags, done=done_today(db, students, moscow_today()),
        stamp_types=stamps.active_types(db), stamp_totals=stamps.balances_by_student(db, [s.id for s in students]),
    )
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
        stamp_types=stamps.active_types(db),
        balances=stamps.balances(db, student.id),
        stamp_rows=stamps.history(db, student.id, limit=20),
        describe=stamps.describe,
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
def group_page(group_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _group_page(request, db, user, own_group(db, user, group_id))


@router.post("/groups/{group_id}/rename")
def rename_group(
    group_id: Id,
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
    """Подсказка логина простым текстом (пусто, если подходящего нет); подставляет её app.js."""
    login = unique_login(db, suggest_login(first_name[:NAME_MAX], last_name[:NAME_MAX]))
    if not is_valid_login(login):
        login = ""
    return PlainTextResponse(login)


@router.get("/groups/{group_id}/students/new")
def new_student_form(
    group_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    return _student_form(request, user, own_group(db, user, group_id), {}, [])


@router.post("/groups/{group_id}/students")
def create_student(
    group_id: Id,
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

    errors = name_errors(first_name, last_name, db)
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
def student_page(
    student_id: Id, request: Request, stamped: bool = False, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    return _student_page(request, db, user, own_student(db, user, student_id), stamped=stamped)


@router.post("/students/{student_id}/edit")
def edit_student(
    student_id: Id,
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
    errors = name_errors(first_name, last_name, db)
    if errors:
        return _student_page(request, db, user, student, errors=errors)
    student.first_name, student.last_name, student.group_id = first_name, last_name, new_group.id
    db.commit()
    return _student_page(request, db, user, student, saved=True)


@router.post("/students/{student_id}/reset-password")
def reset_password(
    student_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    password = generate_temp_password()
    student.password_hash = hash_password(password)
    student.must_change_password = True
    revoke_user_sessions(db, student.id)  # коммитит вместе с новым хешем: старый пароль и сессии умирают разом
    return temp_password_page(request, user, student, password, created=False)


@router.post("/students/{student_id}/unlock")
def unlock_student(student_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    unlock(db, student.login)
    return _to_student(student)


@router.post("/students/{student_id}/deactivate")
def deactivate_student(student_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    student.is_active = False
    revoke_user_sessions(db, student.id)  # коммитит и флаг, и удаление сессий
    return _to_student(student)


@router.post("/students/{student_id}/activate")
def activate_student(student_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    student.is_active = True
    db.commit()
    return _to_student(student)


# ---------- фото ученика (модерация) ----------


@router.post("/students/{student_id}/avatar")
def upload_student_avatar(
    student_id: Id,
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
def delete_student_avatar(student_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    teacher_remove_avatar(db, student)
    return _to_student(student)


@router.post("/students/{student_id}/avatar/history/{item_id}/delete")
def delete_student_avatar_history(
    student_id: Id, item_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    try:
        remove_history_item(db, student, item_id)
    except ValueError:
        raise HTTPException(404) from None
    return _to_student(student)


@router.post("/students/{student_id}/avatar/lock")
def lock_student_avatar(
    student_id: Id, period: str = Form(""), user: User = Depends(teacher_only), db: DbSession = Depends(get_db)
):
    student = own_student(db, user, student_id)
    if period == "":
        return _to_student(student)  # «Оставить как есть»: действующий запрет не трогаем
    if period != "off" and period not in LOCK_PERIODS:
        raise HTTPException(422, "Неизвестный срок")
    set_avatar_lock(db, student, period)
    return _to_student(student)


@router.post("/students/{student_id}/stamps")
def give_stamps(
    student_id: Id,
    request: Request,
    stamp_type_id: str = Form(""),
    amount: str = Form(""),
    action: str = Form("give"),
    comment: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    """Выдать (action=give) или списать (action=take) штампики ученику своей группы."""
    student = own_student(db, user, student_id)
    count = form_int(amount, 1, stamps.AMOUNT_MAX) or 0
    type_id = form_int(stamp_type_id, 1) or 0
    try:
        stamps.give(db, user, student, type_id, -count if action == "take" else count, comment)
    except stamps.StampError as exc:
        values = {"stamp_type_id": stamp_type_id, "amount": amount, "comment": comment}
        return _student_page(request, db, user, student, stamp_error=str(exc), stamp_values=values)
    return RedirectResponse(f"/teacher/students/{student.id}?stamped=1#stamps", status_code=303)
