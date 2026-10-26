"""Ежедневное задание: настройки группы, подбор вопросов на день, попытки и ответы."""

import json
import random
import threading
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.models import Assignment, Attempt, AttemptAnswer, DailySettings, GroupPool, Question, Test, User
from app.questions import grade, grade_pairs, pair_ids, public_view
from app.stamps import award_daily
from app.streak import effective_streak, register_completion
from app.timeutil import moscow_today, utcnow

SEEN_DAYS = 7  # сколько дней до «сегодня» вопросы считаются уже виденными


@dataclass
class TodaySource:
    source: str  # "test" | "pool"
    test_id: int | None
    question_ids: list[int]


def settings_for(db: DbSession, group_id: int) -> DailySettings:
    """Настройки ежедневки группы; если их ещё нет, создаются со значениями по умолчанию."""
    settings = db.get(DailySettings, group_id)
    if settings is None:
        settings = DailySettings(group_id=group_id, question_count=5, max_attempts=3, shuffle_questions=True, shuffle_options=True)
        db.add(settings)
        try:
            db.commit()
        except IntegrityError:  # параллельный запрос успел создать строку раньше
            db.rollback()
            settings = db.get(DailySettings, group_id)
    return settings


def _test_questions(db: DbSession, test_id: int) -> list[int]:
    test = db.get(Test, test_id)
    return [q.id for q in test.questions if q.is_active] if test else []


def _seen_ids(db: DbSession, student_id: int, day: date) -> set[int]:
    rows = db.scalars(
        select(Attempt.question_ids).where(
            Attempt.student_id == student_id, Attempt.date >= day - timedelta(days=SEEN_DAYS), Attempt.date < day
        )
    )
    return {qid for ids in rows for qid in ids}


def _from_pools(db: DbSession, student: User, day: date) -> list[int]:
    count = settings_for(db, student.group_id).question_count
    pool_ids = select(GroupPool.pool_id).where(GroupPool.group_id == student.group_id)
    available = sorted(db.scalars(select(Question.id).where(Question.pool_id.in_(pool_ids), Question.is_active.is_(True))))
    if len(available) <= count:
        return available
    rng = random.Random(f"{student.id}:{day.isoformat()}")
    seen = _seen_ids(db, student.id, day)
    fresh = [q for q in available if q not in seen]
    stale = [q for q in available if q in seen]
    chosen = rng.sample(fresh, min(count, len(fresh)))
    if len(chosen) < count:
        chosen += rng.sample(stale, count - len(chosen))
    return chosen


def todays_source(db: DbSession, student: User, day: date) -> TodaySource | None:
    """Что ученику делать в день day: тест ученику, тест группе, иначе вопросы из пулов группы; нет ничего — None."""
    if student.group_id is None:
        return None
    for target in (Assignment.student_id == student.id, Assignment.group_id == student.group_id):
        test_id = db.scalar(select(Assignment.test_id).where(target, Assignment.date == day))
        if test_id is not None:
            ids = _test_questions(db, test_id)
            if ids:  # пустой тест не показываем: ищем дальше
                return TodaySource("test", test_id, ids)
    ids = _from_pools(db, student, day)
    return TodaySource("pool", None, ids) if ids else None


# ---------- попытки ----------

RAW_LIMIT = 2000  # больше этого «сырой» ответ клиента в истории не храним (настоящие ответы короче)

# Записи попыток (старт, ответ, пары, завершение) идут по одной: «прочитал — проверил — записал»
# без замка дал бы лишнюю попытку при двух одновременных «Начать» или потерял бы ошибку в парах.
# Прод — один процесс uvicorn (--workers 1), поэтому замка в памяти процесса достаточно.
# Внутри замка попытку перечитываем из БД (db.refresh): объект мог быть загружен до ожидания.
_WRITE_LOCK = threading.RLock()


class NoAttemptsLeft(Exception):
    """Попытки на сегодня закончились."""


class NothingToday(Exception):
    """Сегодня заданий нет."""


class AttemptFinished(Exception):
    """Попытка уже завершена: новых ответов не принимаем."""


class AttemptExpired(AttemptFinished):
    """Попытка вчерашняя (по Москве): играть её можно только в свой день."""


class PairsNotDone(ValueError):
    """match_pairs: ответ прислан, а собраны не все пары."""


def seed_for(student_id: int, day: date) -> str:
    """Один seed на ученика и день: им перемешиваются варианты и из него же собираются id плиток и пар."""
    return f"{student_id}:{day.isoformat()}"


def _attempts(db: DbSession, student: User, day: date) -> list[Attempt]:
    return list(db.scalars(select(Attempt).where(Attempt.student_id == student.id, Attempt.date == day).order_by(Attempt.id)))


def live_questions(db: DbSession, attempt: Attempt) -> list[Question]:
    """Вопросы попытки в порядке показа. Скрытые учителем остаются, удалённые совсем — пропускаются."""
    rows = {q.id: q for q in db.scalars(select(Question).where(Question.id.in_(attempt.question_ids)))}
    return [rows[qid] for qid in attempt.question_ids if qid in rows]


def _answers(db: DbSession, attempt: Attempt) -> dict[int, AttemptAnswer]:
    rows = db.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id == attempt.id))
    return {row.question_id: row for row in rows}


def active_attempt(db: DbSession, student: User, now: datetime | None = None) -> Attempt | None:
    """Незавершённая сегодняшняя попытка ученика (самая ранняя)."""
    return db.scalar(
        select(Attempt)
        .where(Attempt.student_id == student.id, Attempt.date == moscow_today(now), Attempt.finished_at.is_(None))
        .order_by(Attempt.id)
        .limit(1)
    )


def todays_status(db: DbSession, student: User, now: datetime | None = None) -> dict:
    """Карточка «Задание на сегодня»: состояние, попытки, лучший результат и серия."""
    day = moscow_today(now)
    attempts = _attempts(db, student, day)
    finished = [a for a in attempts if a.finished_at is not None]
    if any(a.finished_at is None for a in attempts):
        state = "in_progress"
    elif finished:
        state = "done"
    else:
        state = "ready" if todays_source(db, student, day) else "none"
    best = max(((a.correct_count, a.total_count) for a in finished), default=None)
    return {
        "state": state,
        "attempts_used": len(attempts),
        "max_attempts": settings_for(db, student.group_id).max_attempts if student.group_id else 0,
        "best": best,
        "streak": effective_streak(db, student, day),
    }


def start_or_resume(db: DbSession, student: User, now: datetime | None = None) -> Attempt:
    """Незавершённая сегодняшняя попытка или новая (с тем же набором вопросов, что у первой попытки дня)."""
    with _WRITE_LOCK:  # проверка лимита и создание попытки — одним шагом
        return _start_or_resume(db, student, now)


def _start_or_resume(db: DbSession, student: User, now: datetime | None) -> Attempt:
    day = moscow_today(now)
    attempts = _attempts(db, student, day)
    for attempt in attempts:
        if attempt.finished_at is None:
            return attempt
    if student.group_id is None:
        raise NothingToday
    settings = settings_for(db, student.group_id)
    if len(attempts) >= settings.max_attempts:
        raise NoAttemptsLeft
    if attempts:
        source, test_id, ids = attempts[0].source, attempts[0].test_id, list(attempts[0].question_ids)
    else:
        today = todays_source(db, student, day)
        if today is None:
            raise NothingToday
        source, test_id, ids = today.source, today.test_id, list(today.question_ids)
    existing = set(db.scalars(select(Question.id).where(Question.id.in_(ids))))
    ids = [qid for qid in ids if qid in existing]
    if not ids:
        raise NothingToday
    if settings.shuffle_questions:
        random.Random(f"{student.id}:{day.isoformat()}:{len(attempts) + 1}").shuffle(ids)
    attempt = Attempt(
        student_id=student.id, date=day, source=source, test_id=test_id,
        question_ids=ids, pair_mistakes={}, pair_matched={}, total_count=len(ids),
    )
    db.add(attempt)
    db.commit()
    return attempt


def _finish(db: DbSession, attempt: Attempt, live: list[Question], answers: dict[int, AttemptAnswer]) -> None:
    """Завершает попытку (без commit). Серия засчитывается только первой завершённой попыткой дня."""
    others = select(func.count()).select_from(Attempt).where(
        Attempt.student_id == attempt.student_id,
        Attempt.date == attempt.date,
        Attempt.finished_at.is_not(None),
        Attempt.id != attempt.id,
    )
    first = db.scalar(others) == 0
    attempt.finished_at = utcnow()
    attempt.total_count = len(live)
    attempt.correct_count = sum(1 for q in live if q.id in answers and answers[q.id].is_correct)
    student = db.get(User, attempt.student_id)
    if first:
        register_completion(db, student, attempt.date)
    award_daily(db, attempt, student.group_id)


def _current(db: DbSession, attempt: Attempt) -> tuple[list[Question], dict[int, AttemptAnswer], Question | None]:
    """Вопросы попытки, данные ответы и следующий неотвеченный вопрос (None — отвечено всё)."""
    live = live_questions(db, attempt)
    answers = _answers(db, attempt)
    return live, answers, next((q for q in live if q.id not in answers), None)


def progress(db: DbSession, attempt: Attempt) -> tuple[list[Question], int]:
    """Вопросы попытки и сколько из них уже отвечено (ответы идут по порядку — это индекс текущего)."""
    live, answers, _ = _current(db, attempt)
    return live, sum(1 for q in live if q.id in answers)


def finish_if_complete(db: DbSession, attempt: Attempt) -> bool:
    """Неотвеченных вопросов не осталось (например, последний удалили) — завершает попытку. True, если она завершена."""
    with _WRITE_LOCK:
        db.refresh(attempt)
        if attempt.finished_at is not None:
            return True
        live, answers, current = _current(db, attempt)
        if current is not None:
            return False
        _finish(db, attempt, live, answers)
        db.commit()
        return True


def _stored(attempt: Attempt, row: AttemptAnswer, live: list[Question], answers: dict[int, AttemptAnswer]) -> dict:
    """Результат ответа для плеера; next_index — сколько вопросов попытки уже отвечено."""
    return {
        "correct": row.is_correct,
        "typo": row.had_typo,
        "correct_answer": (row.answer or {}).get("correct", ""),
        "finished": attempt.finished_at is not None,
        "next_index": sum(1 for q in live if q.id in answers),
    }


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _shown(question: Question, answer, seed: str, mistakes: int) -> str:
    """Ответ ученика словами — для истории (не меняется от будущих правок вопроса)."""
    p = question.payload
    if question.type == "match_pairs":
        return "без ошибок" if mistakes == 0 else f"ошибок: {mistakes}"
    if not isinstance(answer, dict):
        return ""
    if question.type == "text_input":
        text = answer.get("text")
        return text.strip()[:300] if isinstance(text, str) else ""
    if question.type in ("choice_text", "choice_image"):
        i = answer.get("option")
        if not _is_int(i) or not 0 <= i < len(p["options"]):
            return ""
        option = p["options"][i]
        return option if question.type == "choice_text" else (option["caption"] or f"вариант {i + 1}")
    tiles = answer.get("tiles")  # word_bank: id плиток -> слова (тот же seed, что у плеера)
    if not isinstance(tiles, list):
        return ""
    words = {t["id"]: t["word"] for t in public_view(question, seed, False)["tiles"]}
    return " ".join(words[t] for t in tiles if _is_int(t) and t in words)


def _small(answer) -> dict:
    """«Сырой» ответ клиента для истории; слишком большой не храним."""
    try:
        return answer if isinstance(answer, dict) and len(json.dumps(answer)) <= RAW_LIMIT else {}
    except (TypeError, ValueError):
        return {}


def _check_day(attempt: Attempt, now: datetime | None) -> None:
    if attempt.date != moscow_today(now):
        raise AttemptExpired


def _true_pairs(question: Question, attempt: Attempt) -> set[tuple[int, int]]:
    return set(zip(*pair_ids(question, seed_for(attempt.student_id, attempt.date))))


def _matched(attempt: Attempt, question_id: int) -> list[list[int]]:
    return list((attempt.pair_matched or {}).get(str(question_id), []))


def submit_answer(db: DbSession, attempt: Attempt, question_id: int, answer: dict, now: datetime | None = None) -> dict:
    """Ответ на следующий вопрос попытки. Повтор ответа на уже отвеченный вопрос возвращает сохранённый результат."""
    with _WRITE_LOCK:
        db.refresh(attempt)
        return _submit_answer(db, attempt, question_id, answer, now)


def _submit_answer(db: DbSession, attempt: Attempt, question_id: int, answer: dict, now: datetime | None) -> dict:
    _check_day(attempt, now)
    live, answers, current = _current(db, attempt)
    if question_id in answers:
        return _stored(attempt, answers[question_id], live, answers)
    if attempt.finished_at is not None:
        raise AttemptFinished
    if current is None or current.id != question_id:
        raise ValueError("Это не следующий вопрос попытки")
    if current.type == "match_pairs" and {tuple(p) for p in _matched(attempt, question_id)} != _true_pairs(current, attempt):
        raise PairsNotDone("Собраны не все пары")

    seed = seed_for(attempt.student_id, attempt.date)
    mistakes = int((attempt.pair_mistakes or {}).get(str(question_id), 0))
    result = grade_pairs(current, mistakes) if current.type == "match_pairs" else grade(current, answer, seed)
    row = AttemptAnswer(
        attempt_id=attempt.id,
        question_id=question_id,
        answer={"raw": _small(answer), "shown": _shown(current, answer, seed, mistakes), "correct": result.correct_answer},
        is_correct=result.correct,
        had_typo=result.typo,
        created_at=now or utcnow(),
    )
    attempt_id = attempt.id
    try:
        db.add(row)
        db.flush()
        answers[question_id] = row
        if all(q.id in answers for q in live):
            _finish(db, attempt, live, answers)
        db.commit()
    except IntegrityError:  # тот же ответ пришёл параллельно (двойной тап) и уже записан
        db.rollback()
        attempt = db.get(Attempt, attempt_id)
        answers = _answers(db, attempt)
        return _stored(attempt, answers[question_id], live, answers)
    return _stored(attempt, row, live, answers)


def check_pair(db: DbSession, attempt: Attempt, question_id: int, left: int, right: int, now: datetime | None = None) -> bool:
    """match_pairs: верна ли пара (left, right). Верная запоминается, неверная увеличивает счётчик ошибок вопроса."""
    with _WRITE_LOCK:
        db.refresh(attempt)
        _check_day(attempt, now)
        if attempt.finished_at is not None:
            raise AttemptFinished
        _, _, current = _current(db, attempt)
        if current is None or current.id != question_id or current.type != "match_pairs":
            raise ValueError("Это не текущий вопрос с парами")
        key = str(question_id)
        ok = (left, right) in _true_pairs(current, attempt)
        # JSON-колонки меняем только присваиванием нового dict: изменение «на месте» SQLAlchemy не заметит
        if ok and [left, right] not in _matched(attempt, question_id):
            attempt.pair_matched = {**(attempt.pair_matched or {}), key: _matched(attempt, question_id) + [[left, right]]}
        elif not ok:
            mistakes = dict(attempt.pair_mistakes or {})
            mistakes[key] = int(mistakes.get(key, 0)) + 1
            attempt.pair_mistakes = mistakes
        db.commit()
        return ok
