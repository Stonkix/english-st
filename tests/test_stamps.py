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


# ---------- рисовки и обмен ----------


def _kinds(db):
    """Обычный, редкий, легендарный с рисовками и правилами, как после миграции 0008."""
    from app.models import ConversionRule, StampVariant
    db.query(StampType).delete()
    common = StampType(name="Обычный", icon="star", color="yellow", sort_order=1)
    rare = StampType(name="Редкий", icon="gem", color="blue", sort_order=2)
    legend = StampType(name="Легендарный", icon="crown", color="purple", sort_order=3, craftable=False)
    db.add_all([common, rare, legend])
    db.flush()
    db.add_all([
        StampVariant(stamp_type_id=common.id, icon="star"), StampVariant(stamp_type_id=common.id, icon="apple"),
        StampVariant(stamp_type_id=rare.id, icon="gem"), StampVariant(stamp_type_id=legend.id, icon="crown"),
        ConversionRule(from_type_id=common.id, from_amount=10, to_type_id=rare.id, to_amount=1, is_active=True),
        ConversionRule(from_type_id=rare.id, from_amount=1, to_type_id=common.id, to_amount=10, is_active=True),
        ConversionRule(from_type_id=legend.id, from_amount=1, to_type_id=common.id, to_amount=25, is_active=True),
    ])
    db.commit()
    return common, rare, legend


def _rule(db, src, dst):
    from app.models import ConversionRule
    return db.query(ConversionRule).filter_by(from_type_id=src.id, to_type_id=dst.id).one()


def test_exchange_common_to_rare_and_back(db):
    common, rare, legend = _kinds(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    stamps.give(db, t, kid, common.id, 25)
    got = stamps.exchange(db, kid, _rule(db, common, rare).id, times=2)
    assert got.amount == 2 and got.variant.stamp_type_id == rare.id
    assert stamps.balance_of(db, kid.id, common.id) == 5 and stamps.balance_of(db, kid.id, rare.id) == 2
    with pytest.raises(stamps.StampError, match="нужно 10"):
        stamps.exchange(db, kid, _rule(db, common, rare).id)
    stamps.exchange(db, kid, _rule(db, rare, common).id)
    assert stamps.balance_of(db, kid.id, common.id) == 15 and stamps.balance_of(db, kid.id, rare.id) == 1
    rows = stamps.history(db, kid.id)
    assert rows[0].reason == "conversion" and stamps.describe(rows[0]) == "Обмен: 1 Редкий → 10 Обычный"


def test_legendary_exchanges_down_only(db):
    common, rare, legend = _kinds(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    stamps.give(db, t, kid, legend.id, 1)
    stamps.exchange(db, kid, _rule(db, legend, common).id)
    assert stamps.balance_of(db, kid.id, common.id) == 25 and stamps.balance_of(db, kid.id, legend.id) == 0
    # правило «в легендарный» нельзя завести, а заведённое в обход — не работает
    assert any("нельзя получить обменом" in e for e in stamps.rule_errors(db, common.id, 25, legend.id, 1))
    from app.models import ConversionRule
    bad = ConversionRule(from_type_id=common.id, from_amount=25, to_type_id=legend.id, to_amount=1, is_active=True)
    db.add(bad)
    db.commit()
    assert bad not in stamps.exchange_rules(db)
    with pytest.raises(stamps.StampError, match="Такого обмена нет"):
        stamps.exchange(db, kid, bad.id)


def test_exchange_validation(db):
    common, rare, legend = _kinds(db)
    kid = make_user(db, login="kid")
    rule = _rule(db, common, rare)
    for times in (0, 101):
        with pytest.raises(stamps.StampError):
            stamps.exchange(db, kid, rule.id, times)
    rule.is_active = False
    db.commit()
    with pytest.raises(stamps.StampError):
        stamps.exchange(db, kid, rule.id)
    assert stamps.rule_errors(db, common.id, 1, common.id, 1) == ["Обменивать вид сам на себя нельзя"]


def test_variants_drop_randomly_and_collect(db):
    common, rare, legend = _kinds(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    for _ in range(30):
        stamps.give(db, t, kid, common.id, 1)
    stamps.give(db, t, kid, common.id, -1)  # списание — без рисовки
    assert stamps.history(db, kid.id)[0].variant_id is None
    rows = {r.stamp_type.name: r for r in stamps.collection(db, kid.id)}
    assert rows["Обычный"].owned == 2  # за 30 выдач выпали обе рисовки (вероятность промаха ~1e-9)
    assert rows["Редкий"].owned == 0 and rows["Легендарный"].owned == 0
    # скрытая рисовка: больше не выпадает, но у кого есть — остаётся в коллекции
    apple = next(v for v, _ in rows["Обычный"].variants if v.icon == "apple")
    apple.is_active = False
    db.commit()
    assert all(stamps.pick_variant(db, common.id) != apple.id for _ in range(20))
    assert stamps.collection(db, kid.id)[0].owned == 2
    other = make_user(db, login="other")
    assert [v.icon for v, _ in stamps.collection(db, other.id)[0].variants] == ["star"]
