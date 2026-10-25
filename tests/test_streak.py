from datetime import timedelta

from app.streak import effective_streak, had_tasks, register_completion
from tests.test_daily import DAY, add_pool, add_test, assign, moscow_setup


def set_streak(db, student, current, last, best=None):
    student.current_streak = current
    student.best_streak = best if best is not None else current
    student.last_completed_date = last
    db.commit()


def test_consecutive_days(db):
    _, _, student = moscow_setup(db)
    register_completion(db, student, DAY)
    register_completion(db, student, DAY + timedelta(days=1))
    assert (student.current_streak, student.best_streak) == (2, 2)
    assert student.last_completed_date == DAY + timedelta(days=1)


def test_same_day_twice_counts_once(db):
    _, _, student = moscow_setup(db)
    register_completion(db, student, DAY)
    register_completion(db, student, DAY)
    assert student.current_streak == 1


def test_gap_with_tasks_resets(db):
    teacher, group, student = moscow_setup(db)
    add_pool(db, teacher, group, 2)
    set_streak(db, student, 4, DAY - timedelta(days=2))
    register_completion(db, student, DAY)
    assert student.current_streak == 1
    assert student.best_streak == 4


def test_gap_without_tasks_keeps(db):
    _, _, student = moscow_setup(db)  # у группы нет пулов и назначений
    set_streak(db, student, 4, DAY - timedelta(days=3))
    register_completion(db, student, DAY)
    assert student.current_streak == 5
    assert effective_streak(db, student, DAY) == 5


def test_gap_day_with_assignment_resets(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 1)
    db.get(Question, ids[0]).is_active = False  # пул пуст: заданий нет
    db.commit()
    test = add_test(db, teacher, ids)
    set_streak(db, student, 3, DAY - timedelta(days=2))
    assert not had_tasks(db, student, DAY - timedelta(days=1))
    assign(db, add_test(db, teacher, [add_pool(db, teacher, group, 1)[0]]), day=DAY - timedelta(days=1), student=student)
    assert had_tasks(db, student, DAY - timedelta(days=1))
    assert test is not None


def test_best_kept(db):
    _, _, student = moscow_setup(db)
    set_streak(db, student, 2, DAY - timedelta(days=1), best=9)
    register_completion(db, student, DAY)
    assert (student.current_streak, student.best_streak) == (3, 9)


def test_effective_streak_zero_after_missed_day(db):
    teacher, group, student = moscow_setup(db)
    add_pool(db, teacher, group, 2)
    set_streak(db, student, 5, DAY - timedelta(days=2))
    assert effective_streak(db, student, DAY) == 0
    assert effective_streak(db, student, DAY - timedelta(days=1)) == 5  # вчера последний раз: серия жива
    set_streak(db, student, 5, DAY)
    assert effective_streak(db, student, DAY) == 5


def test_effective_streak_edge_cases(db):
    _, _, student = moscow_setup(db)
    assert effective_streak(db, student, DAY) == 0  # ещё ничего не проходил
    set_streak(db, student, 3, DAY - timedelta(days=500))
    assert effective_streak(db, student, DAY) == 0  # слишком давно


def test_no_group_has_no_tasks(db):
    from tests.factories import make_user

    loner = make_user(db)
    assert not had_tasks(db, loner, DAY)


def test_group_assignment_counts(db):
    teacher, group, student = moscow_setup(db)
    ids = add_pool(db, teacher, group, 1)
    # убираем пул из группы, оставляем только назначение
    from app.models import GroupPool

    db.query(GroupPool).delete()
    db.commit()
    assert not had_tasks(db, student, DAY)
    assign(db, add_test(db, teacher, ids), group=group)
    assert had_tasks(db, student, DAY)
