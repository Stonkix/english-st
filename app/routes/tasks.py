"""Учитель: пулы вопросов, редактор вопросов (в том числе с картинками), тесты и задания групп."""

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession
from starlette.datastructures import FormData, UploadFile

from app.avatars import read_photo
from app.daily import settings_for
from app import stamps
from app.db import get_db
from app.images import InvalidImage, delete_media, is_picture_of, save_picture
from app.models import (
    Assignment, AttemptAnswer, DailyReward, Group, GroupPool, Pool, Question, StampType, Test, TestQuestion, User,
)
from app.models.tasks import LEVELS, QUESTION_TYPES
from app.params import MAX_ID, Id, form_int
from app.questions import PayloadError, picture_paths, validate_payload
from app.results import day_rows, group_students, history, parse_day
from app.routes.teacher import own_group, own_student, teacher_only
from app.templating import templates
from app.timeutil import moscow_today

router = APIRouter(prefix="/tasks")

TITLE_MAX = 80
DESCRIPTION_MAX = 500
PROMPT_MAX = 300

TYPE_LABELS = {
    "choice_text": "Выбор из слов",
    "choice_image": "Выбор картинки",
    "text_input": "Ввод ответа",
    "word_bank": "Собрать фразу",
    "match_pairs": "Соединить пары",
}
TYPE_HINTS = {
    "choice_text": "Вопрос и несколько вариантов, один верный",
    "choice_image": "Вопрос и картинки на выбор, одна верная",
    "text_input": "Ученик вводит слово или фразу сам",
    "word_bank": "Ученик собирает фразу из слов",
    "match_pairs": "Ученик соединяет левую часть с правой",
}
BAD_PICTURE = "Не получилось открыть картинку. Попробуйте другую."
PICTURE_MISSING = "Картинка не найдена, загрузите заново"


# ---------- доступ ----------


def own_pool(db: DbSession, user: User, pool_id: int) -> Pool:
    """Пул учителя (админу — любой); чужой или несуществующий — 404."""
    pool = db.get(Pool, pool_id)
    if pool is None or (user.role != "admin" and pool.owner_id != user.id):
        raise HTTPException(404)
    return pool


def own_question(db: DbSession, user: User, question_id: int) -> Question:
    """Действующий вопрос учителя (админу — любой); скрытый (удалённый) считается несуществующим."""
    question = db.get(Question, question_id)
    if question is None or not question.is_active or (user.role != "admin" and question.owner_id != user.id):
        raise HTTPException(404)
    return question


# ---------- картинки ----------


def _still_used(db: DbSession, owner_id: int, path: str) -> bool:
    return any(path in picture_paths(q) for q in db.scalars(select(Question).where(Question.owner_id == owner_id)))


def _drop_unused(db: DbSession, owner_id: int, paths) -> None:
    """Удаляет с диска файлы, на которые больше не ссылается ни один вопрос автора."""
    for path in paths:
        if not _still_used(db, owner_id, path):
            delete_media(path)


def _delete_all(paths: list[str]) -> None:
    for path in paths:
        delete_media(path)


def _upload(form: FormData, key: str, owner_id: int, saved: list[str]) -> str | None:
    """Сохраняет файл из поля формы (если выбран); путь запоминает в saved — на случай отката."""
    file = form.get(key)
    if not isinstance(file, UploadFile) or not file.filename:
        return None
    try:
        path = save_picture(owner_id, read_photo(file))
    except InvalidImage:
        raise PayloadError(BAD_PICTURE) from None
    saved.append(path)
    return path


def _kept(owner_id: int, path: str) -> str:
    """Прежняя картинка из скрытого поля: годится, только если её сохраняли для этого автора."""
    if not is_picture_of(owner_id, path):
        raise PayloadError(PICTURE_MISSING)
    return path


# ---------- разбор формы вопроса ----------


def _texts(form: FormData, name: str) -> list[str]:
    return [v for v in form.getlist(name) if isinstance(v, str)]


def _int(raw, default: int = -1) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _remap(rows: list, keep, correct: int) -> tuple[list, int]:
    """Выбрасывает пустые строки и пересчитывает номер правильной (-1, если она пустая)."""
    kept, new_correct = [], -1
    for i, row in enumerate(rows):
        if keep(row):
            if i == correct:
                new_correct = len(kept)
            kept.append(row)
    return kept, new_correct


def _parse_payload(form: FormData, qtype: str, owner_id: int, saved: list[str]) -> dict:
    """Payload для проверки validate_payload; новые файлы вариантов сохраняет и складывает в saved."""
    if qtype == "choice_text":
        options, correct = _remap(_texts(form, "options"), lambda t: t.strip(), _int(form.get("correct")))
        return {"options": options, "correct": correct}

    if qtype == "choice_image":
        hidden = _texts(form, "option_images")
        rows = []
        for i, caption in enumerate(_texts(form, "captions")):
            uploaded = _upload(form, f"option_file_{i}", owner_id, saved)
            kept = hidden[i] if i < len(hidden) else ""
            rows.append({"image": uploaded or kept, "caption": caption, "new": bool(uploaded)})
        rows, correct = _remap(rows, lambda r: r["image"] or r["caption"].strip(), _int(form.get("correct")))
        options = []
        for r in rows:
            if not r["image"]:
                raise PayloadError("Загрузите картинку для каждого варианта")
            options.append({"image": r["image"] if r["new"] else _kept(owner_id, r["image"]), "caption": r["caption"]})
        return {"options": options, "correct": correct}

    if qtype == "text_input":
        return {"accepted": [a for a in _texts(form, "accepted") if a.strip()]}

    if qtype == "word_bank":
        return {"answer": str(form.get("answer") or ""), "distractors": _texts(form, "distractors")}

    # match_pairs
    left, right = _texts(form, "left"), _texts(form, "right")
    right += [""] * (len(left) - len(right))
    return {"pairs": [[a, b] for a, b in zip(left, right) if a.strip() or b.strip()]}


def _parse_question(form: FormData, qtype: str, owner_id: int, saved: list[str]):
    """(текст, картинка вопроса, проверенный payload). Бросает PayloadError; новые файлы — в saved."""
    prompt = str(form.get("prompt") or "").strip()
    if not prompt:
        raise PayloadError("Введите текст вопроса")
    if len(prompt) > PROMPT_MAX:
        raise PayloadError(f"Текст вопроса — не длиннее {PROMPT_MAX} символов")
    keep = str(form.get("image_keep") or "")
    image = _upload(form, "image", owner_id, saved)
    if not image and keep and not form.get("image_remove"):
        image = _kept(owner_id, keep)
    return prompt, image, validate_payload(qtype, _parse_payload(form, qtype, owner_id, saved))


def _defaults(qtype: str) -> dict:
    """Что показать в пустой форме: удобное число строк для телефона."""
    base = {"prompt": "", "image": "", "correct": -1}
    if qtype == "choice_text":
        return {**base, "options": ["", "", ""]}
    if qtype == "choice_image":
        return {**base, "rows": [{"image": "", "caption": ""}, {"image": "", "caption": ""}]}
    if qtype == "text_input":
        return {**base, "accepted": [""]}
    if qtype == "word_bank":
        return {**base, "answer": "", "distractors": [""]}
    return {**base, "pairs": [["", ""], ["", ""]]}


def _values_of(question: Question) -> dict:
    """Значения формы по сохранённому вопросу."""
    p = question.payload
    values = {"prompt": question.prompt, "image": question.image or "", "correct": p.get("correct", -1)}
    if question.type == "choice_text":
        values["options"] = list(p["options"])
    elif question.type == "choice_image":
        values["rows"] = [{"image": o["image"], "caption": o["caption"]} for o in p["options"]]
    elif question.type == "text_input":
        values["accepted"] = list(p["accepted"])
    elif question.type == "word_bank":
        values["answer"], values["distractors"] = p["answer"], list(p["distractors"]) or [""]
    else:
        values["pairs"] = [list(pair) for pair in p["pairs"]]
    return values


def _with_defaults(qtype: str, values: dict) -> dict:
    """Дополняет значения пустыми строками, чтобы в форме всегда было что заполнять."""
    merged = {**_defaults(qtype), **values}
    for key in ("options", "rows", "accepted", "distractors", "pairs"):
        if key in merged and not merged[key]:
            merged[key] = _defaults(qtype).get(key, merged[key])
    return merged


# ---------- страницы ----------


def _owner_filter(stmt, user: User, column):
    return stmt if user.role == "admin" else stmt.where(column == user.id)


def _tasks_page(request: Request, db: DbSession, user: User, values: dict | None = None, error: str | None = None):
    pools = list(db.scalars(_owner_filter(select(Pool), user, Pool.owner_id).order_by(Pool.level, Pool.title, Pool.id)))
    tests = list(db.scalars(_owner_filter(select(Test), user, Test.owner_id).order_by(Test.title, Test.id)))
    owner_ids = {p.owner_id for p in pools} | {t.owner_id for t in tests}
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(owner_ids)))} if owner_ids else {}
    context = {
        "user": user, "pools": pools, "tests": tests, "owners": owners, "levels": LEVELS,
        "values": values or {"title": "", "description": "", "level": ""}, "error": error,
    }
    return templates.TemplateResponse(request, "tasks/pools.html", context)


def _pool_page(request: Request, user: User, pool: Pool, **context):
    context.update(user=user, pool=pool, levels=LEVELS, types=QUESTION_TYPES, labels=TYPE_LABELS, hints=TYPE_HINTS)
    return templates.TemplateResponse(request, "tasks/pool.html", context)


def _question_page(
    request: Request, user: User, home: Pool | Test, qtype: str, values: dict, errors: list[str], question=None
):
    """Форма вопроса; home — пул или тест, куда вернётся учитель (и куда добавится новый вопрос)."""
    kind = "pools" if isinstance(home, Pool) else "tests"
    action = f"/tasks/questions/{question.id}" if question else f"/tasks/{kind}/{home.id}/questions"
    context = {
        "user": user, "home": home, "back_url": f"/tasks/{kind}/{home.id}", "qtype": qtype, "label": TYPE_LABELS[qtype], "values": _with_defaults(qtype, values),
        "errors": errors, "question": question, "action": action,
    }
    return templates.TemplateResponse(request, "tasks/question_form.html", context)


def _pool_fields(title: str, description: str, level: str) -> tuple[dict, str | None]:
    values = {"title": title.strip(), "description": description.strip(), "level": level}
    if not values["title"]:
        return values, "Введите название пула"
    if len(values["title"]) > TITLE_MAX:
        return values, f"Название — не длиннее {TITLE_MAX} символов"
    if not values["description"]:
        return values, "Введите описание пула"
    if len(values["description"]) > DESCRIPTION_MAX:
        return values, f"Описание — не длиннее {DESCRIPTION_MAX} символов"
    if level not in LEVELS:
        return values, "Выберите уровень из списка"
    return values, None


def _to_pool(pool: Pool) -> RedirectResponse:
    return RedirectResponse(f"/tasks/pools/{pool.id}", status_code=303)


def _to_home(home: Pool | Test) -> RedirectResponse:
    return RedirectResponse(f"/tasks/{'pools' if isinstance(home, Pool) else 'tests'}/{home.id}", status_code=303)


def home_of(db: DbSession, question: Question) -> Pool | Test | None:
    """Где живёт вопрос: его пул или (для вопроса, созданного прямо в тесте) первый тест с ним."""
    if question.pool_id:
        return db.get(Pool, question.pool_id)
    test_id = db.scalar(select(TestQuestion.test_id).where(TestQuestion.question_id == question.id).order_by(TestQuestion.test_id))
    return db.get(Test, test_id) if test_id else None


# ---------- пулы ----------


@router.get("")
def tasks(request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _tasks_page(request, db, user)


@router.post("/pools")
def create_pool(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    level: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    values, error = _pool_fields(title, description, level)
    if error:
        return _tasks_page(request, db, user, values, error)
    pool = Pool(owner_id=user.id, **values)
    db.add(pool)
    db.commit()
    return _to_pool(pool)


@router.get("/pools/{pool_id}")
def pool_page(pool_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _pool_page(request, user, own_pool(db, user, pool_id))


@router.post("/pools/{pool_id}/edit")
def edit_pool(
    pool_id: Id,
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    level: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    pool = own_pool(db, user, pool_id)
    values, error = _pool_fields(title, description, level)
    if error:
        return _pool_page(request, user, pool, edit_error=error, edit_values=values)
    pool.title, pool.description, pool.level = values["title"], values["description"], values["level"]
    db.commit()
    return _to_pool(pool)


@router.post("/pools/{pool_id}/delete")
def delete_pool(pool_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    pool = own_pool(db, user, pool_id)
    if pool.questions:
        return _pool_page(request, user, pool, delete_error="Сначала удалите вопросы")
    db.delete(pool)
    db.commit()
    return RedirectResponse("/tasks", status_code=303)


# ---------- вопросы ----------


@router.get("/pools/{pool_id}/questions/new")
def new_question(
    pool_id: Id,
    request: Request,
    type: str = Query(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    pool = own_pool(db, user, pool_id)
    if type not in QUESTION_TYPES:
        raise HTTPException(404)
    return _question_page(request, user, pool, type, {}, [])


@router.post("/pools/{pool_id}/questions")
async def create_question(pool_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return await _create_question(request, user, db, own_pool(db, user, pool_id))


async def _create_question(request: Request, user: User, db: DbSession, home: Pool | Test):
    """Новый вопрос в пуле (pool_id = пул) или прямо в тесте (pool_id = None, вопрос встаёт в конец теста)."""
    form = await request.form()
    qtype = str(form.get("type") or "")
    if qtype not in QUESTION_TYPES:
        raise HTTPException(404)
    saved: list[str] = []
    try:
        prompt, image, payload = _parse_question(form, qtype, home.owner_id, saved)
    except PayloadError as exc:
        values = _failed_values(form, qtype, home.owner_id, saved)
        _delete_all(saved)
        return _question_page(request, user, home, qtype, values, [str(exc)])
    try:
        question = Question(
            owner_id=home.owner_id, pool_id=home.id if isinstance(home, Pool) else None,
            type=qtype, prompt=prompt, payload=payload, image=image,
        )
        db.add(question)
        if isinstance(home, Test):
            db.flush()
            db.add(TestQuestion(test_id=home.id, question_id=question.id, position=_next_position(db, home)))
        db.commit()
    except Exception:
        _delete_all(saved)
        raise
    return _to_home(home)


def _failed_values(form: FormData, qtype: str, owner_id: int, saved: list[str]) -> dict:
    """Введённое для повторного показа формы после ошибки (новые файлы уже удалены — их выберут заново)."""
    values = {"prompt": str(form.get("prompt") or ""), "image": str(form.get("image_keep") or "")}
    if qtype == "choice_text":
        values.update(options=_texts(form, "options"), correct=_int(form.get("correct")))
    elif qtype == "choice_image":
        hidden = _texts(form, "option_images")
        rows = [
            {"image": hidden[i] if i < len(hidden) and is_picture_of(owner_id, hidden[i]) else "", "caption": c}
            for i, c in enumerate(_texts(form, "captions"))
        ]
        values.update(rows=rows, correct=_int(form.get("correct")))
    elif qtype == "text_input":
        values["accepted"] = _texts(form, "accepted")
    elif qtype == "word_bank":
        values.update(answer=str(form.get("answer") or ""), distractors=_texts(form, "distractors"))
    else:
        left, right = _texts(form, "left"), _texts(form, "right")
        right += [""] * (len(left) - len(right))
        values["pairs"] = [[a, b] for a, b in zip(left, right)]
    if not is_picture_of(owner_id, values["image"]) or form.get("image_remove"):
        values["image"] = ""
    return values


@router.get("/questions/{question_id}/edit")
def edit_question_page(question_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    question = own_question(db, user, question_id)
    home = home_of(db, question)
    if home is None:
        return RedirectResponse("/tasks", status_code=303)
    return _question_page(request, user, home, question.type, _values_of(question), [], question)


@router.post("/questions/{question_id}")
async def update_question(question_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    question = own_question(db, user, question_id)
    home = home_of(db, question)
    if home is None:
        return RedirectResponse("/tasks", status_code=303)
    form = await request.form()
    saved: list[str] = []
    try:
        prompt, image, payload = _parse_question(form, question.type, question.owner_id, saved)
    except PayloadError as exc:
        values = _failed_values(form, question.type, question.owner_id, saved)
        _delete_all(saved)
        return _question_page(request, user, home, question.type, values, [str(exc)], question)
    old = picture_paths(question)
    try:
        question.prompt, question.image, question.payload = prompt, image, payload
        db.commit()
    except Exception:
        _delete_all(saved)
        raise
    _drop_unused(db, question.owner_id, old - picture_paths(question))
    return _to_home(home)


@router.post("/questions/{question_id}/delete")
def delete_question(question_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    question = own_question(db, user, question_id)
    home = home_of(db, question)
    _remove_question(db, question)
    return _to_home(home) if home else RedirectResponse("/tasks", status_code=303)


def _remove_question(db: DbSession, question: Question) -> None:
    """Удаляет вопрос; если на него ссылается история ответов — только прячет."""
    owner_id, paths = question.owner_id, picture_paths(question)
    answered = db.scalar(select(AttemptAnswer.question_id).where(AttemptAnswer.question_id == question.id).limit(1))
    if answered is not None:
        question.is_active = False  # на вопрос ссылается история ответов: прячем, картинки оставляем
        db.commit()
    else:
        db.delete(question)
        db.commit()
        _drop_unused(db, owner_id, paths)


# ---------- тесты ----------


def own_test(db: DbSession, user: User, test_id: int) -> Test:
    """Тест учителя (админу — любой); чужой или несуществующий — 404."""
    test = db.get(Test, test_id)
    if test is None or (user.role != "admin" and test.owner_id != user.id):
        raise HTTPException(404)
    return test


def _next_position(db: DbSession, test: Test) -> int:
    last = db.scalar(select(func.max(TestQuestion.position)).where(TestQuestion.test_id == test.id))
    return 0 if last is None else last + 1


def _pools_of(db: DbSession, owner_id: int) -> list[Pool]:
    return list(db.scalars(select(Pool).where(Pool.owner_id == owner_id).order_by(Pool.level, Pool.title, Pool.id)))


def _tests_page(request: Request, db: DbSession, user: User, values: dict | None = None, error: str | None = None):
    tests = list(db.scalars(_owner_filter(select(Test), user, Test.owner_id).order_by(Test.title, Test.id)))
    owner_ids = {t.owner_id for t in tests}
    owners = {u.id: u for u in db.scalars(select(User).where(User.id.in_(owner_ids)))} if owner_ids else {}
    context = {"user": user, "tests": tests, "owners": owners, "values": values or {"title": "", "description": ""}, "error": error}
    return templates.TemplateResponse(request, "tasks/tests.html", context)


def _test_page(request: Request, db: DbSession, user: User, test: Test, pool: Pool | None = None, error: str | None = None):
    items = [q for q in test.questions if q.is_active]
    in_test = {q.id for q in test.questions}
    context = {
        "user": user, "test": test, "items": items, "pools": _pools_of(db, test.owner_id), "pool": pool,
        "candidates": [q for q in pool.questions if q.id not in in_test] if pool else [],
        "error": error, "types": QUESTION_TYPES, "labels": TYPE_LABELS, "hints": TYPE_HINTS,
    }
    return templates.TemplateResponse(request, "tasks/test.html", context)


@router.get("/tests")
def tests_page(request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _tests_page(request, db, user)


@router.post("/tests")
def create_test(
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    values = {"title": title.strip(), "description": description.strip()}
    error = None
    if not values["title"]:
        error = "Введите название теста"
    elif len(values["title"]) > TITLE_MAX:
        error = f"Название — не длиннее {TITLE_MAX} символов"
    elif len(values["description"]) > DESCRIPTION_MAX:
        error = f"Описание — не длиннее {DESCRIPTION_MAX} символов"
    if error:
        return _tests_page(request, db, user, values, error)
    test = Test(owner_id=user.id, **values)
    db.add(test)
    db.commit()
    return _to_home(test)


@router.get("/tests/{test_id}")
def test_page(
    test_id: Id,
    request: Request,
    pool: str = Query(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    test = own_test(db, user, test_id)
    chosen = None
    if pool:
        chosen = db.get(Pool, int(pool)) if pool.isdigit() and int(pool) <= MAX_ID else None
        if chosen is None or chosen.owner_id != test.owner_id:
            raise HTTPException(404)
    return _test_page(request, db, user, test, chosen)


@router.post("/tests/{test_id}/add")
def add_question(
    test_id: Id,
    request: Request,
    question_id: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    """Добавляет в тест существующий вопрос из пулов автора теста."""
    test = own_test(db, user, test_id)
    if not question_id.isdigit() or not 1 <= int(question_id) <= MAX_ID:
        raise HTTPException(404)
    question = own_question(db, user, int(question_id))
    if question.pool_id is None or question.owner_id != test.owner_id:
        raise HTTPException(404)
    if db.get(TestQuestion, (test.id, question.id)) is not None:
        return _test_page(request, db, user, test, db.get(Pool, question.pool_id), "Этот вопрос уже в тесте")
    db.add(TestQuestion(test_id=test.id, question_id=question.id, position=_next_position(db, test)))
    try:
        db.commit()
    except IntegrityError:  # двойное нажатие: вопрос уже добавлен параллельным запросом
        db.rollback()
    return RedirectResponse(f"/tasks/tests/{test.id}?pool={question.pool_id}", status_code=303)


@router.get("/tests/{test_id}/questions/new")
def new_test_question(
    test_id: Id,
    request: Request,
    type: str = Query(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    test = own_test(db, user, test_id)
    if type not in QUESTION_TYPES:
        raise HTTPException(404)
    return _question_page(request, user, test, type, {}, [])


@router.post("/tests/{test_id}/questions")
async def create_test_question(test_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return await _create_question(request, user, db, own_test(db, user, test_id))


@router.post("/tests/{test_id}/questions/{question_id}/move")
def move_question(
    test_id: Id,
    question_id: Id,
    direction: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    """Сдвигает вопрос на одно место вверх или вниз среди действующих вопросов теста."""
    test = own_test(db, user, test_id)
    if direction not in ("up", "down"):
        raise HTTPException(404)
    rows = list(db.scalars(select(TestQuestion).where(TestQuestion.test_id == test.id).order_by(TestQuestion.position, TestQuestion.question_id)))
    active = {q.id for q in test.questions if q.is_active}
    index = next((i for i, r in enumerate(rows) if r.question_id == question_id), None)
    if index is None:
        raise HTTPException(404)
    step = -1 if direction == "up" else 1
    other = index + step
    while 0 <= other < len(rows) and rows[other].question_id not in active:  # скрытые вопросы перешагиваем
        other += step
    if 0 <= other < len(rows):
        rows[index], rows[other] = rows[other], rows[index]
    for position, row in enumerate(rows):
        row.position = position
    db.commit()
    return _to_home(test)


@router.post("/tests/{test_id}/questions/{question_id}/remove")
def remove_from_test(test_id: Id, question_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    """Убирает вопрос из теста; вопрос, созданный прямо в тесте и больше нигде не нужный, удаляется совсем."""
    test = own_test(db, user, test_id)
    row = db.get(TestQuestion, (test.id, question_id))
    if row is None:
        raise HTTPException(404)
    db.delete(row)
    db.commit()
    question = db.get(Question, question_id)
    if question is not None and question.pool_id is None and home_of(db, question) is None:
        _remove_question(db, question)
    return _to_home(test)


# ---------- задания группы ----------

COUNT_RANGE = "Число вопросов — от 1 до 30"
ATTEMPTS_RANGE = "Попыток — от 1 до 10"
DATE_PASSED = "Дата уже прошла"
DATE_TAKEN = "На эту дату уже назначен тест"


def _group_tasks_page(request: Request, db: DbSession, user: User, group: Group, **context):
    today = moscow_today()
    students = sorted(
        (s for s in group.students if s.role == "student"), key=lambda s: (s.last_name.lower(), s.first_name.lower(), s.id)
    )
    by_id = {s.id: s for s in students}
    assignments = list(
        db.scalars(
            select(Assignment)
            .where(Assignment.date >= today, (Assignment.group_id == group.id) | Assignment.student_id.in_(by_id))
            .order_by(Assignment.date, Assignment.id)
        )
    )
    tests = list(db.scalars(select(Test).where(Test.owner_id == group.teacher_id).order_by(Test.title, Test.id)))
    settings = settings_for(db, group.id)
    context.setdefault("settings_values", {
        "question_count": settings.question_count, "max_attempts": settings.max_attempts,
        "shuffle_questions": settings.shuffle_questions, "shuffle_options": settings.shuffle_options,
    })
    context.update(
        user=user, group=group, students=students, today=today, tests=tests, assignments=assignments,
        titles={t.id: t.title for t in tests}, by_id=by_id,
        pools=_pools_of(db, group.teacher_id),
        connected=set(db.scalars(select(GroupPool.pool_id).where(GroupPool.group_id == group.id))),
        rewards=list(db.scalars(select(DailyReward).where(DailyReward.group_id == group.id).order_by(DailyReward.id))),
        stamp_types=stamps.active_types(db),
    )
    return templates.TemplateResponse(request, "tasks/group_daily.html", context)


def _to_group_tasks(group: Group) -> RedirectResponse:
    return RedirectResponse(f"/tasks/groups/{group.id}", status_code=303)


@router.get("/groups/{group_id}")
def group_tasks(group_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _group_tasks_page(request, db, user, own_group(db, user, group_id))


@router.post("/groups/{group_id}/pools")
async def save_group_pools(group_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    """Заменяет набор пулов группы; чужие пулы (не учителя группы) молча пропускаются."""
    group = own_group(db, user, group_id)
    form = await request.form()
    wanted = {int(v) for v in form.getlist("pool_ids") if isinstance(v, str) and v.isdigit() and int(v) <= MAX_ID}
    allowed = {p.id for p in _pools_of(db, group.teacher_id)} & wanted
    for link in db.scalars(select(GroupPool).where(GroupPool.group_id == group.id)):
        if link.pool_id in allowed:
            allowed.discard(link.pool_id)
        else:
            db.delete(link)
    db.add_all(GroupPool(group_id=group.id, pool_id=pool_id) for pool_id in allowed)
    db.commit()
    return _to_group_tasks(group)


def _in_range(raw: str, low: int, high: int) -> int | None:
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if low <= value <= high else None


@router.post("/groups/{group_id}/settings")
def save_group_settings(
    group_id: Id,
    request: Request,
    question_count: str = Form(""),
    max_attempts: str = Form(""),
    shuffle_questions: str = Form(""),
    shuffle_options: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    count, attempts = _in_range(question_count.strip(), 1, 30), _in_range(max_attempts.strip(), 1, 10)
    errors = [text for value, text in ((count, COUNT_RANGE), (attempts, ATTEMPTS_RANGE)) if value is None]
    if errors:
        values = {
            "question_count": question_count, "max_attempts": max_attempts,
            "shuffle_questions": bool(shuffle_questions), "shuffle_options": bool(shuffle_options),
        }
        return _group_tasks_page(request, db, user, group, settings_errors=errors, settings_values=values)
    settings = settings_for(db, group.id)
    settings.question_count, settings.max_attempts = count, attempts
    settings.shuffle_questions, settings.shuffle_options = bool(shuffle_questions), bool(shuffle_options)
    db.commit()
    return _to_group_tasks(group)


def _assignment_error(db: DbSession, group: Group, test_id: str, day: str, target: str):
    """(test_id, student_id | None, дата) или текст ошибки."""
    test = db.get(Test, int(test_id)) if test_id.isdigit() and int(test_id) <= MAX_ID else None
    if test is None or test.owner_id != group.teacher_id:
        return "Выберите тест из списка"
    try:
        when = date.fromisoformat(day)
    except ValueError:
        return "Укажите дату"
    if when < moscow_today():
        return DATE_PASSED
    if target == "group":
        return test.id, None, when
    student = db.get(User, int(target)) if target.isdigit() and int(target) <= MAX_ID else None
    if student is None or student.role != "student" or student.group_id != group.id:
        return "Выберите, кому назначить тест"
    return test.id, student.id, when


@router.post("/groups/{group_id}/assignments")
def add_assignment(
    group_id: Id,
    request: Request,
    test_id: str = Form(""),
    day: str = Form("", alias="date"),
    target: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    result = _assignment_error(db, group, test_id.strip(), day.strip(), target.strip())
    values = {"test_id": test_id, "date": day, "target": target}
    if isinstance(result, str):
        return _group_tasks_page(request, db, user, group, assign_error=result, assign_values=values)
    chosen_test, student_id, when = result
    owner = Assignment.group_id == group.id if student_id is None else Assignment.student_id == student_id
    if db.scalar(select(Assignment.id).where(owner, Assignment.date == when)) is not None:
        return _group_tasks_page(request, db, user, group, assign_error=DATE_TAKEN, assign_values=values)
    db.add(Assignment(test_id=chosen_test, group_id=group.id if student_id is None else None, student_id=student_id, date=when))
    try:
        db.commit()
    except IntegrityError:  # параллельный запрос успел раньше
        db.rollback()
        return _group_tasks_page(request, db, user, group, assign_error=DATE_TAKEN, assign_values=values)
    return _to_group_tasks(group)


@router.post("/groups/{group_id}/assignments/{assignment_id}/delete")
def delete_assignment(group_id: Id, assignment_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    group = own_group(db, user, group_id)
    assignment = db.get(Assignment, assignment_id)
    student = db.get(User, assignment.student_id) if assignment and assignment.student_id else None
    if assignment is None or (assignment.group_id != group.id and (student is None or student.group_id != group.id)):
        raise HTTPException(404)
    db.delete(assignment)
    db.commit()
    return _to_group_tasks(group)


# ---------- результаты ----------


@router.get("/groups/{group_id}/results")
def group_results(
    group_id: Id,
    request: Request,
    day: str | None = Query(None),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    """Итоги дня по ученикам группы; day — YYYY-MM-DD, неверный или будущий день заменяется сегодняшним."""
    group = own_group(db, user, group_id)
    today = moscow_today()
    chosen = parse_day(day, today)
    context = {
        "user": user, "group": group, "day": chosen, "today": today,
        "prev_day": chosen - timedelta(days=1), "next_day": chosen + timedelta(days=1),
        "rows": day_rows(db, group_students(group), chosen, today),
    }
    return templates.TemplateResponse(request, "tasks/results_day.html", context)


@router.get("/students/{student_id}/results")
def student_results(student_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    student = own_student(db, user, student_id)
    context = {"user": user, "student": student, "entries": history(db, student, moscow_today())}
    return templates.TemplateResponse(request, "tasks/student_results.html", context)


# ---------- награды за ежедневное задание ----------

REWARD_INVALID = f"Награда: выберите вид, количество от 1 до {stamps.REWARD_MAX} и порог от 0 до 100%"


@router.post("/groups/{group_id}/rewards")
def add_reward(
    group_id: Id,
    request: Request,
    stamp_type_id: str = Form(""),
    amount: str = Form(""),
    min_score_percent: str = Form("0"),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    type_id = form_int(stamp_type_id, 1)
    count = form_int(amount, 1, stamps.REWARD_MAX)
    percent = form_int(min_score_percent or "0", 0, 100)
    stamp_type = db.get(StampType, type_id) if type_id else None
    if stamp_type is None or not stamp_type.is_active or count is None or percent is None:
        values = {"stamp_type_id": stamp_type_id, "amount": amount, "min_score_percent": min_score_percent}
        return _group_tasks_page(request, db, user, group, reward_error=REWARD_INVALID, reward_values=values)
    db.add(DailyReward(group_id=group.id, stamp_type_id=stamp_type.id, amount=count, min_score_percent=percent))
    db.commit()
    return _to_group_tasks(group)


@router.post("/groups/{group_id}/rewards/{reward_id}/delete")
def delete_reward(group_id: Id, reward_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    group = own_group(db, user, group_id)
    reward = db.get(DailyReward, reward_id)
    if reward is None or reward.group_id != group.id:
        raise HTTPException(404)
    db.delete(reward)  # начисленное остаётся в журнале (reward_id станет пустым)
    db.commit()
    return _to_group_tasks(group)
