"""Учитель: пулы вопросов и редактор вопросов (в том числе с картинками)."""

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.datastructures import FormData, UploadFile

from app.avatars import read_photo
from app.db import get_db
from app.images import InvalidImage, delete_media, is_picture_of, save_picture
from app.models import AttemptAnswer, Pool, Question, Test, User
from app.models.tasks import LEVELS, QUESTION_TYPES
from app.params import Id
from app.questions import PayloadError, picture_paths, validate_payload
from app.routes.teacher import teacher_only
from app.templating import templates

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


def _question_page(request: Request, user: User, pool: Pool, qtype: str, values: dict, errors: list[str], question=None):
    action = f"/tasks/questions/{question.id}" if question else f"/tasks/pools/{pool.id}/questions"
    context = {
        "user": user, "pool": pool, "qtype": qtype, "label": TYPE_LABELS[qtype], "values": _with_defaults(qtype, values),
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
    pool = own_pool(db, user, pool_id)
    form = await request.form()
    qtype = str(form.get("type") or "")
    if qtype not in QUESTION_TYPES:
        raise HTTPException(404)
    saved: list[str] = []
    try:
        prompt, image, payload = _parse_question(form, qtype, pool.owner_id, saved)
    except PayloadError as exc:
        values = _failed_values(form, qtype, pool.owner_id, saved)
        _delete_all(saved)
        return _question_page(request, user, pool, qtype, values, [str(exc)])
    try:
        db.add(Question(owner_id=pool.owner_id, pool_id=pool.id, type=qtype, prompt=prompt, payload=payload, image=image))
        db.commit()
    except Exception:
        _delete_all(saved)
        raise
    return _to_pool(pool)


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
    pool = db.get(Pool, question.pool_id) if question.pool_id else None
    if pool is None:
        return RedirectResponse("/tasks", status_code=303)
    return _question_page(request, user, pool, question.type, _values_of(question), [], question)


@router.post("/questions/{question_id}")
async def update_question(question_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    question = own_question(db, user, question_id)
    pool = db.get(Pool, question.pool_id) if question.pool_id else None
    if pool is None:
        return RedirectResponse("/tasks", status_code=303)
    form = await request.form()
    saved: list[str] = []
    try:
        prompt, image, payload = _parse_question(form, question.type, question.owner_id, saved)
    except PayloadError as exc:
        values = _failed_values(form, question.type, question.owner_id, saved)
        _delete_all(saved)
        return _question_page(request, user, pool, question.type, values, [str(exc)], question)
    old = picture_paths(question)
    try:
        question.prompt, question.image, question.payload = prompt, image, payload
        db.commit()
    except Exception:
        _delete_all(saved)
        raise
    _drop_unused(db, question.owner_id, old - picture_paths(question))
    return _to_pool(pool)


@router.post("/questions/{question_id}/delete")
def delete_question(question_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    question = own_question(db, user, question_id)
    pool_id, owner_id, paths = question.pool_id, question.owner_id, picture_paths(question)
    answered = db.scalar(select(AttemptAnswer.question_id).where(AttemptAnswer.question_id == question.id).limit(1))
    if answered is not None:
        question.is_active = False  # на вопрос ссылается история ответов: прячем, картинки оставляем
        db.commit()
    else:
        db.delete(question)
        db.commit()
        _drop_unused(db, owner_id, paths)
    return RedirectResponse(f"/tasks/pools/{pool_id}" if pool_id else "/tasks", status_code=303)
