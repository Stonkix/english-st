"""Ученик: главная с заданием на сегодня, плеер задания и экран результата."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import home_for, require_role, require_user
from app.daily import (
    AttemptFinished,
    active_attempt,
    NoAttemptsLeft,
    NothingToday,
    check_pair,
    finish_if_complete,
    progress,
    seed_for,
    settings_for,
    start_or_resume,
    submit_answer,
    todays_status,
)
from app import stamps
from app.db import get_db
from app.models import Attempt, User
from app.params import MAX_ID, Id
from app.questions import public_view
from app.templating import templates
from app.timeutil import moscow_today

router = APIRouter()

student_only = require_role("student")

# JSON-адреса плеера: ошибки здесь отдаются JSON, а не HTML-страницей (см. main.py)
JSON_PATHS = {"/daily/answer", "/daily/pair"}


class AnswerIn(BaseModel):
    attempt_id: int = Field(ge=1, le=MAX_ID)
    question_id: int = Field(ge=1, le=MAX_ID)
    answer: dict


class PairIn(BaseModel):
    attempt_id: int = Field(ge=1, le=MAX_ID)
    question_id: int = Field(ge=1, le=MAX_ID)
    left: int = Field(ge=0, le=MAX_ID)
    right: int = Field(ge=0, le=MAX_ID)


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def _own_attempt(db: DbSession, student: User, attempt_id: int) -> Attempt | None:
    attempt = db.get(Attempt, attempt_id)
    return attempt if attempt is not None and attempt.student_id == student.id else None


def _home_page(request: Request, db: DbSession, user: User, notice: str | None = None):
    status = todays_status(db, user)
    left = max(0, status["max_attempts"] - status["attempts_used"])
    context = {"user": user, "status": status, "left": left, "notice": notice, "balances": stamps.balances(db, user.id)}
    return templates.TemplateResponse(request, "student/home.html", context)


NO_ATTEMPTS = "Попытки на сегодня закончились. Приходи завтра!"


@router.get("/")
def home(request: Request, limit: str | None = None, user: User = Depends(require_user), db: DbSession = Depends(get_db)):
    if user.role != "student":
        return RedirectResponse(home_for(user), status_code=303)
    return _home_page(request, db, user, NO_ATTEMPTS if limit is not None else None)


@router.post("/daily/start")
def start(user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        start_or_resume(db, user)
    except NoAttemptsLeft:
        return RedirectResponse("/?limit=1", status_code=303)
    except NothingToday:
        return RedirectResponse("/", status_code=303)
    return RedirectResponse("/daily/play", status_code=303)


@router.get("/daily/play")
def play(request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    attempt = active_attempt(db, user)
    if attempt is None:
        return RedirectResponse("/", status_code=303)
    if finish_if_complete(db, attempt):  # оставшиеся вопросы удалены — попытка закончена
        return RedirectResponse(f"/daily/result/{attempt.id}", status_code=303)
    seed = seed_for(user.id, attempt.date)
    shuffle = settings_for(db, user.group_id).shuffle_options if user.group_id else True
    live, index = progress(db, attempt)
    data = {
        "attempt_id": attempt.id,
        "index": index,
        "questions": [public_view(q, seed, shuffle) for q in live],
        # уже собранные пары match_pairs: после обновления страницы плеер покажет их собранными
        "matched": attempt.pair_matched or {},
    }
    # меню не показываем (user в шаблон не передаём): плеер на весь экран
    return templates.TemplateResponse(request, "student/player.html", {"data": data})


@router.post("/daily/answer")
def answer(body: AnswerIn, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    attempt = _own_attempt(db, user, body.attempt_id)
    if attempt is None:
        return _error(404, "Попытка не найдена")
    try:
        return submit_answer(db, attempt, body.question_id, body.answer)
    except AttemptFinished:
        return _error(409, "Попытка уже завершена")
    except ValueError:
        return _error(409, "Этот вопрос сейчас не задан")


@router.post("/daily/pair")
def pair(body: PairIn, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    attempt = _own_attempt(db, user, body.attempt_id)
    if attempt is None:
        return _error(404, "Попытка не найдена")
    try:
        return {"ok": check_pair(db, attempt, body.question_id, body.left, body.right)}
    except AttemptFinished:
        return _error(409, "Попытка уже завершена")
    except ValueError:
        return _error(409, "Этот вопрос сейчас не задан")


@router.get("/daily/result/{attempt_id}")
def result(attempt_id: Id, request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    attempt = _own_attempt(db, user, attempt_id)
    if attempt is None:
        return templates.TemplateResponse(request, "404.html", {"user": user}, status_code=404)
    if attempt.finished_at is None:
        return RedirectResponse("/daily/play", status_code=303)
    status = todays_status(db, user)
    can_retry = attempt.date == moscow_today() and status["state"] == "done" and status["attempts_used"] < status["max_attempts"]
    awarded = stamps.awarded_for(db, attempt.id)
    context = {"attempt": attempt, "streak": status["streak"], "can_retry": can_retry, "awarded": awarded}
    return templates.TemplateResponse(request, "student/result.html", context)


@router.get("/stamps")
def my_stamps(request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    context = {
        "user": user,
        "balances": stamps.balances(db, user.id),
        "rows": stamps.history(db, user.id, limit=100),
        "describe": stamps.describe,
    }
    return templates.TemplateResponse(request, "student/stamps.html", context)
