"""Результаты для учителя: итоги дня по группе и история ученика."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Attempt, AttemptAnswer, Question, Test, User
from app.streak import effective_streak

HISTORY_DAYS = 14
MAX_BACK_DAYS = 3650  # глубже не листаем: защита от дат вроде 0001-01-01
DELETED_QUESTION = "Вопрос удалён"


def parse_day(raw: str | None, today: date) -> date:
    """День из ?day=YYYY-MM-DD; неверный или будущий — сегодня."""
    try:
        day = date.fromisoformat(raw or "")
    except ValueError:
        return today
    if day > today or day < today - timedelta(days=MAX_BACK_DAYS):
        return today
    return day


def group_students(group) -> list[User]:
    return sorted(
        (s for s in group.students if s.role == "student"), key=lambda s: (s.last_name.lower(), s.first_name.lower(), s.id)
    )


def _attempts_by_student(db: DbSession, student_ids, start: date, end: date) -> dict[int, list[Attempt]]:
    rows = db.scalars(
        select(Attempt)
        .where(Attempt.student_id.in_(list(student_ids)), Attempt.date >= start, Attempt.date <= end)
        .order_by(Attempt.id)
    )
    result: dict[int, list[Attempt]] = {}
    for a in rows:
        result.setdefault(a.student_id, []).append(a)
    return result


def day_rows(db: DbSession, students: list[User], day: date, today: date) -> list[dict]:
    """По ученику: статус, лучший и первый результат, число попыток, серия."""
    by_student = _attempts_by_student(db, [s.id for s in students], day, day)
    rows = []
    for s in students:
        attempts = by_student.get(s.id, [])
        finished = [a for a in attempts if a.finished_at is not None]
        if finished:
            status = "done"
        elif attempts:
            status = "progress" if day == today else "abandoned"
        else:
            status = "none"
        rows.append({
            "student": s,
            "status": status,
            "best": max(((a.correct_count, a.total_count) for a in finished), default=None),
            "first": (finished[0].correct_count, finished[0].total_count) if finished else None,
            "attempts": len(attempts),
            "streak": effective_streak(db, s, today),
        })
    return rows


def done_today(db: DbSession, students: list[User], today: date) -> int:
    """Сколько учеников уже завершили хотя бы одну попытку сегодня."""
    by_student = _attempts_by_student(db, [s.id for s in students], today, today)
    return sum(1 for atts in by_student.values() if any(a.finished_at is not None for a in atts))


def history(db: DbSession, student: User, today: date) -> list[dict]:
    """Попытки за последние 14 дней, новые сверху; у каждой — ответы из снимка."""
    start = today - timedelta(days=HISTORY_DAYS - 1)
    attempts = list(db.scalars(
        select(Attempt)
        .where(Attempt.student_id == student.id, Attempt.date >= start, Attempt.date <= today)
        .order_by(Attempt.date.desc(), Attempt.id.desc())
    ))
    if not attempts:
        return []
    ids = {a.id for a in attempts}
    answers: dict[tuple[int, int], AttemptAnswer] = {
        (r.attempt_id, r.question_id): r
        for r in db.scalars(select(AttemptAnswer).where(AttemptAnswer.attempt_id.in_(ids)))
    }
    question_ids = {qid for a in attempts for qid in a.question_ids}
    prompts = {q.id: q.prompt for q in db.scalars(select(Question).where(Question.id.in_(question_ids)))}
    test_ids = {a.test_id for a in attempts if a.test_id}
    titles = {t.id: t.title for t in db.scalars(select(Test).where(Test.id.in_(test_ids)))} if test_ids else {}
    result = []
    for a in attempts:
        items = []
        for qid in a.question_ids:
            row = answers.get((a.id, qid))
            snap = row.answer if row is not None and isinstance(row.answer, dict) else {}
            items.append({
                "prompt": prompts.get(qid, DELETED_QUESTION),
                "answered": row is not None,
                "correct": bool(row and row.is_correct),
                "typo": bool(row and row.had_typo),
                "shown": snap.get("shown", ""),
                "right": snap.get("correct", ""),
            })
        if a.source == "test":
            source = f"Тест «{titles[a.test_id]}»" if a.test_id in titles else "Тест"
        else:
            source = "Пул вопросов"
        result.append({"attempt": a, "source": source, "answers": items, "finished": a.finished_at is not None})
    return result
