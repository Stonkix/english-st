from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app.models import Assignment, Attempt, AttemptAnswer, Group, Pool, Question, Test, User


def _teacher(db):
    t = User(login="t1", password_hash="x", role="teacher", first_name="Анна", last_name="С")
    db.add(t)
    db.flush()
    return t


def _setup(db):
    t = _teacher(db)
    g = Group(name="5А", teacher_id=t.id)
    s = User(login="s1", password_hash="x", role="student", first_name="Петя", last_name="К")
    test = Test(owner_id=t.id, title="Тест", description="")
    db.add_all([g, s, test])
    db.flush()
    return t, g, s, test


def test_assignment_exactly_one_target(db):
    _, g, s, test = _setup(db)
    db.add(Assignment(test_id=test.id, group_id=g.id, student_id=s.id, date=date(2026, 10, 26)))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    _, g, s, test = _setup(db)
    db.add(Assignment(test_id=test.id, date=date(2026, 10, 26)))
    with pytest.raises(IntegrityError):
        db.commit()


def test_assignment_unique_per_group_day(db):
    _, g, s, test = _setup(db)
    day = date(2026, 10, 26)
    db.add(Assignment(test_id=test.id, group_id=g.id, date=day))
    db.commit()
    db.add(Assignment(test_id=test.id, group_id=g.id, date=day))
    with pytest.raises(IntegrityError):
        db.commit()


def test_answer_once_per_question(db):
    t, g, s, test = _setup(db)
    q = Question(owner_id=t.id, type="text_input", prompt="?", payload={"answers": ["a"]})
    a = Attempt(student_id=s.id, date=date(2026, 10, 26), source="test", question_ids=[1], total_count=1)
    db.add_all([q, a])
    db.flush()
    db.add(AttemptAnswer(attempt_id=a.id, question_id=q.id, answer="a", is_correct=True, had_typo=False))
    db.commit()
    db.add(AttemptAnswer(attempt_id=a.id, question_id=q.id, answer="b", is_correct=False, had_typo=False))
    with pytest.raises(IntegrityError):
        db.commit()


def test_pool_questions_hide_inactive(db):
    t = _teacher(db)
    p = Pool(owner_id=t.id, title="П", description="д", level="A1")
    db.add(p)
    db.flush()
    db.add_all(
        [
            Question(owner_id=t.id, pool_id=p.id, type="text_input", prompt="2", payload={}),
            Question(owner_id=t.id, pool_id=p.id, type="text_input", prompt="скрыт", payload={}, is_active=False),
            Question(owner_id=t.id, pool_id=p.id, type="text_input", prompt="3", payload={}),
        ]
    )
    db.commit()
    db.refresh(p)
    assert [q.prompt for q in p.questions] == ["2", "3"]


def test_question_pool_set_null_on_pool_delete(db):
    t = _teacher(db)
    p = Pool(owner_id=t.id, title="П", description="д", level="A1")
    db.add(p)
    db.flush()
    q = Question(owner_id=t.id, pool_id=p.id, type="text_input", prompt="?", payload={})
    db.add(q)
    db.commit()
    db.delete(p)
    db.commit()
    db.refresh(q)
    assert q.pool_id is None
