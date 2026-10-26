"""Админ: раздел «Школа» — учителя (создание, временный пароль, сброс, отключение), словарь фильтра мата, виды штампиков."""

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.accounts import LOGIN_INVALID, LOGIN_TAKEN, login_taken, name_errors, temp_password_page
from app.auth.deps import require_role
from app.auth.login_guard import reset_all
from app.auth.passwords import generate_temp_password, hash_password
from app.auth.sessions import revoke_user_sessions
from app.db import get_db
from app.logins import is_valid_login, normalize_login
from app import stamps
from app.models import BadWord, BadWordException, ReplacementWord, StampType, User
from app.profanity import clean_text, compile_filter, contains_profanity, is_cyrillic, load_filter
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


# ---------- словарь фильтра мата ----------

FILTER_WORD_MAX = 40
FILTER_LIST_MAX = 2000


def _words(text: str) -> list[str]:
    """Слова по одному в строке: без пустых строк и повторов, в нижнем регистре, порядок сохраняется."""
    seen: dict[str, None] = {}
    for line in text.splitlines():
        word = " ".join(line.split()).lower()
        if word:
            seen.setdefault(word, None)
    return list(seen)


def _filter_page(request: Request, db: DbSession, user: User, values: dict | None = None,
                 errors: list[str] | None = None, saved: bool = False, check: str = ""):
    if values is None:
        values = {
            "bad_words": "\n".join(db.scalars(select(BadWord.pattern).order_by(BadWord.id))),
            "exceptions": "\n".join(db.scalars(select(BadWordException.word).order_by(BadWordException.id))),
            "replacements": "\n".join(db.scalars(select(ReplacementWord.word).order_by(ReplacementWord.id))),
        }
    checked = clean_text(check[:1000], load_filter(db))[0] if check.strip() else None
    context = {"user": user, "values": values, "errors": errors or [], "saved": saved, "check": check, "checked": checked}
    return templates.TemplateResponse(request, "school/filter.html", context)


@router.get("/filter")
def filter_form(request: Request, saved: bool = False, check: str = "", user: User = Depends(admin_only),
                db: DbSession = Depends(get_db)):
    return _filter_page(request, db, user, saved=saved, check=check)


@router.post("/filter")
def filter_save(
    request: Request,
    bad_words: str = Form(""),
    exceptions: str = Form(""),
    replacements: str = Form(""),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    values = {"bad_words": bad_words, "exceptions": exceptions, "replacements": replacements}
    bad, exc, rep = _words(bad_words), _words(exceptions), _words(replacements)
    errors = []
    if any(len(w) > FILTER_WORD_MAX for w in bad + exc + rep):
        errors.append(f"Каждое слово — не длиннее {FILTER_WORD_MAX} символов")
    if max(len(bad), len(exc), len(rep)) > FILTER_LIST_MAX:
        errors.append(f"В каждом списке — не больше {FILTER_LIST_MAX} слов")
    if not rep:
        errors.append("Нужно хотя бы одно слово-замена")
    flt = compile_filter(bad, exc, rep)
    rude = [w for w in rep if contains_profanity(w, flt)]
    if rude:
        errors.append("Слова-замены сами попадают под фильтр: " + ", ".join(rude))
    if errors:
        return _filter_page(request, db, user, values=values, errors=errors)
    db.execute(delete(BadWord))
    db.execute(delete(BadWordException))
    db.execute(delete(ReplacementWord))
    db.add_all([BadWord(pattern=w, lang="ru" if is_cyrillic(w) else "en") for w in bad])
    db.add_all([BadWordException(word=w) for w in exc])
    db.add_all([ReplacementWord(word=w) for w in rep])
    db.commit()
    return RedirectResponse("/school/filter?saved=1", status_code=303)


# ---------- виды штампиков ----------


def _stamps_page(request: Request, db: DbSession, user: User, errors: dict | None = None, values: dict | None = None):
    context = {
        "user": user, "types": stamps.all_types(db), "icons": stamps.ICONS, "colors": stamps.COLORS,
        "errors": errors or {}, "values": values or {},
    }
    return templates.TemplateResponse(request, "school/stamps.html", context)


def _stamp_errors(db: DbSession, name: str, icon: str, color: str, sort_order: str, own_id: int | None) -> list[str]:
    errors = []
    if not name or len(name) > stamps.NAME_MAX:
        errors.append(f"Название — от 1 до {stamps.NAME_MAX} символов")
    elif db.scalar(select(StampType.id).where(StampType.name == name, StampType.id != (own_id or 0))) is not None:
        errors.append("Такое название уже есть")
    if icon not in stamps.ICONS or color not in stamps.COLORS:
        errors.append("Выберите картинку и цвет")
    if not sort_order.strip().lstrip("-").isdigit() or abs(int(sort_order)) > 1000:
        errors.append("Порядок — число от -1000 до 1000")
    return errors


@router.get("/stamps")
def stamp_types(request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    return _stamps_page(request, db, user)


@router.post("/stamps")
def create_stamp_type(
    request: Request,
    name: str = Form(""),
    icon: str = Form(""),
    color: str = Form(""),
    sort_order: str = Form("0"),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    name = " ".join(name.split())
    errors = _stamp_errors(db, name, icon, color, sort_order, None)
    if errors:
        values = {"name": name, "icon": icon, "color": color, "sort_order": sort_order}
        return _stamps_page(request, db, user, errors={"new": errors}, values={"new": values})
    db.add(StampType(name=name, icon=icon, color=color, sort_order=int(sort_order)))
    db.commit()
    return RedirectResponse("/school/stamps", status_code=303)


@router.post("/stamps/{type_id}")
def edit_stamp_type(
    type_id: Id,
    request: Request,
    name: str = Form(""),
    icon: str = Form(""),
    color: str = Form(""),
    sort_order: str = Form("0"),
    is_active: str = Form(""),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    """Удалять виды нельзя (на них ссылается журнал) — только отключать: отключённый не выдаётся и не начисляется."""
    stamp_type = db.get(StampType, type_id)
    if stamp_type is None:
        raise HTTPException(404)
    name = " ".join(name.split())
    errors = _stamp_errors(db, name, icon, color, sort_order, stamp_type.id)
    if errors:
        values = {"name": name, "icon": icon, "color": color, "sort_order": sort_order, "is_active": bool(is_active)}
        return _stamps_page(request, db, user, errors={stamp_type.id: errors}, values={stamp_type.id: values})
    stamp_type.name, stamp_type.icon, stamp_type.color = name, icon, color
    stamp_type.sort_order, stamp_type.is_active = int(sort_order), bool(is_active)
    db.commit()
    return RedirectResponse("/school/stamps", status_code=303)
