"""Админ: раздел «Школа» — учителя (создание, временный пароль, сброс, отключение), словарь фильтра мата, виды штампиков, темы вопросов, питомцы."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app import pets, stamps
from app.accounts import LOGIN_INVALID, LOGIN_TAKEN, login_taken, name_errors, temp_password_page
from app.auth.deps import require_role
from app.auth.login_guard import reset_all
from app.auth.passwords import generate_temp_password, hash_password
from app.auth.sessions import revoke_user_sessions
from app.db import get_db
from app.images import InvalidImage, delete_media, read_limited, save_stamp_art
from app.logins import is_valid_login, normalize_login
from app.models import (
    BadWord, BadWordException, ConversionRule, Pool, ReplacementWord, StampTransaction, StampType, StampVariant, Topic, User,
)
from app.params import Id, form_int
from app.profanity import clean_text, compile_filter, contains_profanity, is_cyrillic, load_filter
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
        "errors": errors or {}, "values": values or {}, "rules": stamps.all_rules(db),
        "dropped": set(db.scalars(select(StampTransaction.variant_id).where(StampTransaction.variant_id.is_not(None)).distinct())),
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
    craftable: str = Form(""),
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
        values = {
            "name": name, "icon": icon, "color": color, "sort_order": sort_order,
            "is_active": bool(is_active), "craftable": bool(craftable),
        }
        return _stamps_page(request, db, user, errors={stamp_type.id: errors}, values={stamp_type.id: values})
    stamp_type.name, stamp_type.icon, stamp_type.color = name, icon, color
    stamp_type.sort_order, stamp_type.is_active = int(sort_order), bool(is_active)
    stamp_type.craftable = bool(craftable)  # правила обмена «в этот вид» перестают работать сразу (см. stamps._usable)
    db.commit()
    return RedirectResponse("/school/stamps", status_code=303)



def _to_stamps(anchor: str = "") -> RedirectResponse:
    return RedirectResponse("/school/stamps" + anchor, status_code=303)


# ---------- рисовки ----------


@router.post("/stamps/{type_id}/variants")
def add_variant(
    type_id: Id,
    request: Request,
    icon: str = Form(""),
    name: str = Form(""),
    image: UploadFile | None = File(None),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    """Новая рисовка вида: загруженная картинка (лучше PNG с прозрачным фоном) или встроенная."""
    stamp_type = db.get(StampType, type_id)
    if stamp_type is None:
        raise HTTPException(404)
    name = " ".join(name.split())[:stamps.NAME_MAX]
    rel = None
    try:
        if image is not None and image.filename:
            rel = save_stamp_art(read_limited(image.file))
    except InvalidImage:
        return _stamps_page(request, db, user, errors={f"v{type_id}": ["Картинка не подошла: это не изображение или файл больше 10 МБ"]})
    if rel is None and icon not in stamps.ICONS:
        return _stamps_page(request, db, user, errors={f"v{type_id}": ["Загрузите картинку или выберите встроенную"]})
    order = max((v.sort_order for v in stamp_type.variants), default=0) + 1
    db.add(StampVariant(stamp_type_id=stamp_type.id, name=name, icon=None if rel else icon, image=rel, sort_order=order))
    db.commit()
    return _to_stamps(f"#type-{stamp_type.id}")


def _variant_or_404(db: DbSession, variant_id: int) -> StampVariant:
    variant = db.get(StampVariant, variant_id)
    if variant is None:
        raise HTTPException(404)
    return variant


@router.post("/stamps/variants/{variant_id}/toggle")
def toggle_variant(variant_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    """Скрытая рисовка больше не выпадает, но у кого уже есть — остаётся в коллекции."""
    variant = _variant_or_404(db, variant_id)
    variant.is_active = not variant.is_active
    db.commit()
    return _to_stamps(f"#type-{variant.stamp_type_id}")


@router.post("/stamps/variants/{variant_id}/delete")
def delete_variant(variant_id: Id, request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    """Удалить можно только рисовку, которая ещё никому не выпадала (иначе пропала бы из коллекций)."""
    variant = _variant_or_404(db, variant_id)
    if db.scalar(select(StampTransaction.id).where(StampTransaction.variant_id == variant.id).limit(1)) is not None:
        return _stamps_page(request, db, user, errors={f"v{variant.stamp_type_id}": ["Эта рисовка уже у учеников — её можно только скрыть"]})
    type_id, image = variant.stamp_type_id, variant.image
    db.delete(variant)
    db.commit()
    delete_media(image)
    return _to_stamps(f"#type-{type_id}")


# ---------- правила обмена ----------


@router.post("/stamp-rules")
def add_rule(
    request: Request,
    from_type_id: str = Form(""),
    from_amount: str = Form(""),
    to_type_id: str = Form(""),
    to_amount: str = Form(""),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    values = [form_int(v, 1) or 0 for v in (from_type_id, from_amount, to_type_id, to_amount)]
    errors = stamps.rule_errors(db, *values)
    if errors:
        raw = {"from_type_id": from_type_id, "from_amount": from_amount, "to_type_id": to_type_id, "to_amount": to_amount}
        return _stamps_page(request, db, user, errors={"rule": errors}, values={"rule": raw})
    src, src_amount, dst, dst_amount = values
    db.add(ConversionRule(from_type_id=src, from_amount=src_amount, to_type_id=dst, to_amount=dst_amount, is_active=True))
    db.commit()
    return _to_stamps("#rules")


def _rule_or_404(db: DbSession, rule_id: int) -> ConversionRule:
    rule = db.get(ConversionRule, rule_id)
    if rule is None:
        raise HTTPException(404)
    return rule


@router.post("/stamp-rules/{rule_id}/toggle")
def toggle_rule(rule_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    rule = _rule_or_404(db, rule_id)
    rule.is_active = not rule.is_active
    db.commit()
    return _to_stamps("#rules")


@router.post("/stamp-rules/{rule_id}/delete")
def delete_rule(rule_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    db.delete(_rule_or_404(db, rule_id))  # прошлые обмены остаются в журнале (текст правила — в комментарии)
    db.commit()
    return _to_stamps("#rules")


# ---------- темы вопросов ----------

TOPIC_NAME_MAX = 40


def _topics_page(request: Request, db: DbSession, user: User, error: str | None = None, error_for: str = ""):
    topics = list(db.scalars(select(Topic).order_by(Topic.sort_order, Topic.name)))
    used = dict(db.execute(select(Pool.topic_id, func.count()).where(Pool.topic_id.is_not(None)).group_by(Pool.topic_id)).all())
    context = {"user": user, "topics": topics, "used": used, "error": error, "error_for": error_for}
    return templates.TemplateResponse(request, "school/topics.html", context, status_code=400 if error else 200)


def _topic_error(db: DbSession, name: str, own_id: int | None) -> str | None:
    if not name or len(name) > TOPIC_NAME_MAX:
        return f"Название темы — от 1 до {TOPIC_NAME_MAX} символов"
    # сравниваем в Python: lower() в SQLite не знает кириллицу
    taken = db.scalars(select(Topic.name).where(Topic.id != (own_id or 0)))
    if name.casefold() in {n.casefold() for n in taken}:
        return "Такая тема уже есть"
    return None


@router.get("/topics")
def topics_page(request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    return _topics_page(request, db, user)


@router.post("/topics")
def create_topic(request: Request, name: str = Form(""), user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    name = " ".join(name.split())
    error = _topic_error(db, name, None)
    if error:
        return _topics_page(request, db, user, error, "new")
    order = (db.scalar(select(func.max(Topic.sort_order))) or 0) + 1
    db.add(Topic(name=name, sort_order=order))
    db.commit()
    return RedirectResponse("/school/topics", status_code=303)


@router.post("/topics/{topic_id}")
def edit_topic(
    topic_id: Id,
    request: Request,
    name: str = Form(""),
    sort_order: str = Form("0"),
    user: User = Depends(admin_only),
    db: DbSession = Depends(get_db),
):
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(404)
    name = " ".join(name.split())
    error = _topic_error(db, name, topic.id)
    order = form_int(sort_order, -1000, 1000)
    if error or order is None:
        return _topics_page(request, db, user, error or "Порядок — число от -1000 до 1000", str(topic.id))
    topic.name, topic.sort_order = name, order
    db.commit()
    return RedirectResponse("/school/topics", status_code=303)


@router.post("/topics/{topic_id}/delete")
def delete_topic(topic_id: Id, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    """Пулы темы становятся «без темы», а число вопросов темы у групп пропадает."""
    topic = db.get(Topic, topic_id)
    if topic is None:
        raise HTTPException(404)
    db.delete(topic)
    db.commit()
    return RedirectResponse("/school/topics", status_code=303)


# ---------- питомцы ----------


def _pets_page(request: Request, db: DbSession, user: User, error: str | None = None, saved: bool = False):
    context = {
        "user": user, "cfg": pets.config(db), "species": pets.species_list(db, active_only=False),
        "stamp_types": stamps.active_types(db), "error": error, "saved": saved,
    }
    return templates.TemplateResponse(request, "school/pets.html", context, status_code=400 if error else 200)


@router.get("/pets")
def pets_settings(request: Request, saved: bool = False, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    return _pets_page(request, db, user, saved=saved)


@router.post("/pets")
async def save_pets_settings(request: Request, user: User = Depends(admin_only), db: DbSession = Depends(get_db)):
    """Валюта, корм, сколько дней без ухода терпит питомец; цены и доступность видов."""
    form = await request.form()

    def get(key: str) -> str:
        return str(form.get(key, "") or "")

    cfg = pets.config(db)
    currency = db.get(StampType, form_int(get("price_type_id"), 1) or 0) if form_int(get("price_type_id"), 1) else None
    values = {
        "food_pack_price": form_int(get("food_pack_price"), 1, 1000),
        "food_pack_size": form_int(get("food_pack_size"), 1, 100),
        "food_max": form_int(get("food_max"), 1, 1000),
        "runaway_days": form_int(get("runaway_days"), 1, 30),
    }
    if currency is None or not currency.is_active:
        return _pets_page(request, db, user, "Выберите вид штампиков для цен")
    if None in values.values():
        return _pets_page(request, db, user, "Проверьте числа: цена пачки 1–1000, порций в пачке 1–100, запас 1–1000, дней 1–30")
    if values["food_max"] < values["food_pack_size"]:
        return _pets_page(request, db, user, "Запас корма должен вмещать хотя бы одну пачку")
    species = pets.species_list(db, active_only=False)
    prices = {sp.id: form_int(get(f"price_{sp.id}"), 1, 10000) for sp in species}
    if None in prices.values():
        return _pets_page(request, db, user, "Цена питомца — от 1 до 10000")
    cfg.price_type_id = currency.id
    for key, value in values.items():
        setattr(cfg, key, value)
    for sp in species:
        sp.price, sp.is_active = prices[sp.id], bool(form.get(f"active_{sp.id}"))
    db.commit()
    return RedirectResponse("/school/pets?saved=1", status_code=303)
