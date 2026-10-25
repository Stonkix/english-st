"""Серия дней: сколько дней подряд ученик проходил ежедневное задание."""

from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Assignment, GroupPool, Question, TestQuestion, User

MAX_GAP_DAYS = 400  # глубже пропуски не разбираем: серия считается потерянной


def had_tasks(db: DbSession, student: User, day: date) -> bool:
    """Было ли у ученика задание в день day.

    История подключения пулов не хранится, поэтому пулы считаются постоянными:
    есть хотя бы один пул группы с действующим вопросом или назначение на этот день
    (группе либо ученику) с действующим вопросом в тесте.
    """
    if student.group_id is None:
        return False
    in_pools = select(Question.id).where(
        Question.pool_id.in_(select(GroupPool.pool_id).where(GroupPool.group_id == student.group_id)),
        Question.is_active.is_(True),
    )
    if db.scalar(in_pools.limit(1)) is not None:
        return True
    in_tests = (
        select(TestQuestion.test_id)
        .join(Question, Question.id == TestQuestion.question_id)
        .join(Assignment, Assignment.test_id == TestQuestion.test_id)
        .where(
            Question.is_active.is_(True),
            Assignment.date == day,
            (Assignment.student_id == student.id) | (Assignment.group_id == student.group_id),
        )
    )
    return db.scalar(in_tests.limit(1)) is not None


def _missed_day(db: DbSession, student: User, last: date, day: date) -> bool:
    """Был ли между last и day (не включая их) день с заданиями. Слишком большой разрыв — считаем, что был."""
    gap = (day - last).days - 1
    if gap > MAX_GAP_DAYS:
        return True
    return any(had_tasks(db, student, last + timedelta(days=n)) for n in range(1, gap + 1))


def register_completion(db: DbSession, student: User, day: date) -> None:
    """Первое завершённое задание дня. Только flush: commit делает вызывающий вместе с попыткой."""
    last = student.last_completed_date
    if last is not None and last >= day:
        return
    if last is not None and not _missed_day(db, student, last, day):
        student.current_streak += 1
    else:
        student.current_streak = 1
    student.best_streak = max(student.best_streak, student.current_streak)
    student.last_completed_date = day
    db.flush()


def effective_streak(db: DbSession, student: User, today: date) -> int:
    """Серия для показа: обнуляется, если после последнего прохождения был пропущен день с заданиями."""
    last = student.last_completed_date
    if last is None:
        return 0
    if last >= today - timedelta(days=1):
        return student.current_streak
    return 0 if _missed_day(db, student, last, today) else student.current_streak
