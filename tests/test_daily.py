from datetime import date, datetime, timedelta

from app.daily import settings_for, todays_source
from app.models import Assignment, Attempt, GroupPool, Pool, Question, Test, TestQuestion
from app.timeutil import moscow_today
from tests.factories import make_group, make_user

DAY = date(2026, 10, 26)


def moscow_setup(db):
    teacher = make_user(db, role="teacher")
    group = make_group(db, teacher)
    student = make_user(db, group=group)
    return teacher, group, student


def add_pool(db, teacher, group, count, active=True):
    pool = Pool(owner_id=teacher.id, title="Пул", description="d", level="A1")
    db.add(pool)
    db.flush()
    questions = [
        Question(owner_id=teacher.id, pool_id=pool.id, type="text_input", prompt=f"q{i}", payload={"accepted": ["a"]}, is_active=active)
        for i in range(count)
    ]
    db.add_all(questions)
    db.add(GroupPool(group_id=group.id, pool_id=pool.id))
    db.commit()
    return [q.id for q in questions]


def add_test(db, teacher, questions, title="Тест"):
    test = Test(owner_id=teacher.id, title=title, description="")
    db.add(test)
    db.flush()
    for pos, q in enumerate(questions):
        db.add(TestQuestion(test_id=test.id, question_id=q, position=pos))
    db.commit()
    return test


def assign(db, test, day=DAY, group=None, student=None):
    db.add(Assignment(test_id=test.id, group_id=group.id if group else None, student_id=student.id if student else None, date=day))
    db.commit()


def test_moscow_day_boundary():
    assert moscow_today(datetime(2026, 10, 25, 20, 59)) == date(2026, 10, 25)  # 23:59 МСК
    assert moscow_today(datetime(2026, 10, 25, 21, 0)) == date(2026, 10, 26)  # 00:00 МСК


def test_settings_for_creates_defaults(db):
    _, group, _ = moscow_setup(db)
    s = settings_for(db, group.id)
    assert (s.question_count, s.max_attempts, s.shuffle_questions, s.shuffle_options) == (5, 3, True, True)
    assert settings_for(db, group.id) is s


def test_student_assignment_beats_group(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 6)
    group_test = add_test(db, teacher, ids[:2], "Группе")
    own_test = add_test(db, teacher, ids[2:5], "Ученику")
    assign(db, group_test, group=group)
    assert todays_source(db, student, DAY).test_id == group_test.id
    assign(db, own_test, student=student)
    src = todays_source(db, student, DAY)
    assert (src.source, src.test_id, src.question_ids) == ("test", own_test.id, ids[2:5])
    other = make_user(db, group=group)  # назначение ученику других не касается
    assert todays_source(db, other, DAY).test_id == group_test.id


def test_test_questions_in_position_order_and_only_active(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 4)
    test = add_test(db, teacher, [ids[3], ids[0], ids[2], ids[1]])
    db.get(Question, ids[2]).is_active = False
    db.commit()
    assign(db, test, group=group)
    assert todays_source(db, student, DAY).question_ids == [ids[3], ids[0], ids[1]]


def test_empty_test_falls_through_to_pools(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 3)
    empty = add_test(db, teacher, [])
    assign(db, empty, group=group)
    src = todays_source(db, student, DAY)
    assert src.source == "pool" and src.test_id is None and sorted(src.question_ids) == sorted(ids)


def test_assignment_only_for_its_day(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 6)
    assign(db, add_test(db, teacher, ids[:1]), day=DAY + timedelta(days=1), group=group)
    assert todays_source(db, student, DAY).source == "pool"


def test_pool_selection_deterministic_and_sized(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 12)
    first = todays_source(db, student, DAY)
    assert first.source == "pool" and len(first.question_ids) == 5 == len(set(first.question_ids))
    assert set(first.question_ids) <= set(ids)
    assert todays_source(db, student, DAY).question_ids == first.question_ids
    other_day = todays_source(db, student, DAY + timedelta(days=1)).question_ids
    assert len(other_day) == 5


def test_pool_respects_question_count_setting(db):
    teacher, group, student = moscow_setup(db)
    add_pool(db, teacher, group, 12)
    settings_for(db, group.id).question_count = 8
    db.commit()
    assert len(todays_source(db, student, DAY).question_ids) == 8


def attempt(db, student, day, question_ids):
    db.add(Attempt(student_id=student.id, date=day, source="pool", question_ids=question_ids, total_count=len(question_ids)))
    db.commit()


def test_pool_avoids_last_7_days(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 12)
    attempt(db, student, DAY - timedelta(days=1), ids[:5])
    for day in (DAY, DAY + timedelta(days=1)):
        assert not set(todays_source(db, student, day).question_ids) & set(ids[:5])


def test_pool_tops_up_from_seen_when_fresh_not_enough(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 7)
    attempt(db, student, DAY - timedelta(days=2), ids[:5])
    chosen = todays_source(db, student, DAY).question_ids
    assert len(chosen) == 5 == len(set(chosen))
    assert set(ids[5:]) <= set(chosen)  # свежие взяты все, остальное добрано из виденных


def test_seen_window_is_exactly_7_days_before_day(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 10)
    attempt(db, student, DAY - timedelta(days=8), ids[:5])  # старше недели: не считается
    attempt(db, student, DAY, ids[5:])  # сам день не считается
    chosen = todays_source(db, student, DAY).question_ids
    assert len(chosen) == 5
    assert chosen == todays_source(db, student, DAY).question_ids
    attempt(db, student, DAY - timedelta(days=7), ids[:5])  # ровно 7 дней назад: считается
    assert not set(todays_source(db, student, DAY).question_ids) & set(ids[:5])


def test_other_students_attempts_do_not_matter(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 5)
    attempt(db, make_user(db, group=group), DAY - timedelta(days=1), ids)
    assert sorted(todays_source(db, student, DAY).question_ids) == sorted(ids)


def test_fewer_questions_than_count(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 3)
    assert sorted(todays_source(db, student, DAY).question_ids) == sorted(ids)


def test_no_source_returns_none(db):
    teacher, group, student = moscow_setup(db)
    assert todays_source(db, student, DAY) is None
    add_pool(db, teacher, group, 0)
    assert todays_source(db, student, DAY) is None
    assert todays_source(db, make_user(db), DAY) is None  # ученик без группы


def test_inactive_questions_excluded(db):
    teacher, group, student = moscow_setup(db)
    add_pool(db, teacher, group, 6, active=False)
    assert todays_source(db, student, DAY) is None
    ids = add_pool(db, teacher, group, 2)
    assert sorted(todays_source(db, student, DAY).question_ids) == sorted(ids)


# ---------- темы ----------


def _topic_setup(db, per_topic):
    """Группа с пулами «Грамматика»/«Лексика» и пулом без темы; per_topic — вопросов в каждом пуле."""
    from app.models import GroupPool, Pool, Question, Topic
    db.query(Topic).delete()
    teacher = make_user(db, role="teacher", login="teach")
    group = make_group(db, teacher)
    grammar, vocab = Topic(name="Грамматика", sort_order=1), Topic(name="Лексика", sort_order=2)
    db.add_all([grammar, vocab])
    db.flush()
    ids = {}
    for key, topic in (("grammar", grammar), ("vocab", vocab), ("none", None)):
        pool = Pool(owner_id=teacher.id, title=key, description="d", level="A1", topic_id=topic.id if topic else None)
        db.add(pool)
        db.flush()
        qs = [Question(owner_id=teacher.id, pool_id=pool.id, type="text_input", prompt=f"{key}{i}", payload={"accepted": ["x"]})
              for i in range(per_topic)]
        db.add_all(qs)
        db.add(GroupPool(group_id=group.id, pool_id=pool.id))
        db.flush()
        ids[key] = {q.id for q in qs}
    db.commit()
    return group, grammar, vocab, ids


def test_topic_quotas_pick_from_each_topic(db):
    from app.models import GroupTopicQuota
    group, grammar, vocab, ids = _topic_setup(db, 6)
    db.add_all([GroupTopicQuota(group_id=group.id, topic_id=grammar.id, count=3),
                GroupTopicQuota(group_id=group.id, topic_id=vocab.id, count=2)])
    db.commit()
    kid = make_user(db, login="kid", group=group)
    source = todays_source(db, kid, date(2026, 10, 26))
    picked = set(source.question_ids)
    assert len(picked) == 5 and len(picked & ids["grammar"]) == 3 and len(picked & ids["vocab"]) == 2
    assert todays_source(db, kid, date(2026, 10, 26)).question_ids == source.question_ids  # тот же набор весь день


def test_topic_shortfall_is_filled_from_other_questions(db):
    from app.models import GroupTopicQuota
    group, grammar, vocab, ids = _topic_setup(db, 2)
    db.add(GroupTopicQuota(group_id=group.id, topic_id=grammar.id, count=4))  # в теме всего 2
    db.commit()
    kid = make_user(db, login="kid", group=group)
    picked = set(todays_source(db, kid, date(2026, 10, 26)).question_ids)
    assert len(picked) == 4 and ids["grammar"] <= picked


def test_without_quotas_question_count_applies(db):
    group, grammar, vocab, ids = _topic_setup(db, 4)
    kid = make_user(db, login="kid", group=group)
    assert len(todays_source(db, kid, date(2026, 10, 26)).question_ids) == 5  # question_count по умолчанию
