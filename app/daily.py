"""Ежедневное задание: настройки группы и подбор вопросов на день."""

import random
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Assignment, Attempt, DailySettings, GroupPool, Question, Test, User

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
        db.commit()
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
