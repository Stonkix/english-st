import pytest
from sqlalchemy.exc import IntegrityError

from app import stamps
from app.daily import start_or_resume, submit_answer
from app.models import DailyReward, StampTransaction, StampType
from tests.factories import make_user
from tests.test_results import setup_group


def _types(db):
    db.query(StampType).delete()  # первый тест сессии видит виды из миграции
    star = StampType(name="Звезда", icon="star", color="yellow", sort_order=1)
    gem = StampType(name="Кристалл", icon="gem", color="blue", sort_order=2)
    db.add_all([star, gem])
    db.commit()
    return star, gem


def _play(db, student, questions, right: int):
    """Попытка, где первые right ответов верные (вопросы — word0, word1… в порядке попытки не важен)."""
    attempt = start_or_resume(db, student)
    answers = {q.id: f"word{i}" for i, q in enumerate(questions)}
    for n, qid in enumerate(attempt.question_ids):
        submit_answer(db, attempt, qid, {"text": answers[qid] if n < right else "нет"})
    db.refresh(attempt)
    return attempt


def test_give_and_take(db):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    s = make_user(db, login="kid")
    stamps.give(db, t, s, star.id, 5, "  молодец  ")
    stamps.give(db, t, s, star.id, -2)
    assert stamps.balance_of(db, s.id, star.id) == 3
    with pytest.raises(stamps.StampError, match="у ученика 3"):
        stamps.give(db, t, s, star.id, -4)
    with pytest.raises(stamps.StampError):
        stamps.give(db, t, s, star.id, 0)
    with pytest.raises(stamps.StampError):
        stamps.give(db, t, s, star.id, 1001)
    gem.is_active = False
    db.commit()
    with pytest.raises(stamps.StampError):
        stamps.give(db, t, s, gem.id, 1)
    rows = stamps.history(db, s.id)
    assert [r.amount for r in rows] == [-2, 5] and rows[1].comment == "молодец" and rows[1].actor_id == t.id
    assert stamps.describe(rows[1]) == "От учителя: молодец"


def test_balances_show_active_and_leftovers(db):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    s = make_user(db, login="kid")
    assert [(b.stamp_type.name, b.amount) for b in stamps.balances(db, s.id)] == [("Звезда", 0), ("Кристалл", 0)]
    stamps.give(db, t, s, gem.id, 2)
    star.is_active = gem.is_active = False
    db.commit()
    assert [(b.stamp_type.name, b.amount) for b in stamps.balances(db, s.id)] == [("Кристалл", 2)]


def test_journal_is_append_only_by_constraint(db):
    star, _ = _types(db)
    s = make_user(db, login="kid")
    db.add(StampTransaction(student_id=s.id, stamp_type_id=star.id, amount=0, reason="manual"))
    with pytest.raises(IntegrityError):
        db.commit()


def test_daily_reward_once_per_rule_per_day(db):
    star, gem = _types(db)
    _, group, qs = setup_group(db, 2)
    kid = make_user(db, group=group, login="kid")
    db.add_all([
        DailyReward(group_id=group.id, stamp_type_id=star.id, amount=1, min_score_percent=0),
        DailyReward(group_id=group.id, stamp_type_id=gem.id, amount=3, min_score_percent=100),
    ])
    db.commit()
    first = _play(db, kid, qs, right=1)  # 50%: только звезда
    assert [(t.stamp_type_id, t.amount) for t in stamps.awarded_for(db, first.id)] == [(star.id, 1)]
    second = _play(db, kid, qs, right=2)  # 100%: звезда уже была сегодня, кристалл — впервые
    assert [(t.stamp_type_id, t.amount) for t in stamps.awarded_for(db, second.id)] == [(gem.id, 3)]
    _play(db, kid, qs, right=2)
    assert stamps.balance_of(db, kid.id, star.id) == 1 and stamps.balance_of(db, kid.id, gem.id) == 3


def test_no_reward_without_rules_or_for_inactive_type(db):
    star, _ = _types(db)
    _, group, qs = setup_group(db, 1)
    kid = make_user(db, group=group, login="kid")
    _play(db, kid, qs, right=1)
    assert db.query(StampTransaction).count() == 0
    db.add(DailyReward(group_id=group.id, stamp_type_id=star.id, amount=1, min_score_percent=0))
    star.is_active = False
    db.commit()
    _play(db, kid, qs, right=1)
    assert db.query(StampTransaction).count() == 0
