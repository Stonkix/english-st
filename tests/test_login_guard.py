import threading
from datetime import datetime, timedelta

from app.auth import login_guard as g
from app.models import LoginAttempt

T0 = datetime(2026, 10, 25, 12, 0)


def test_block_after_five(db):
    for i in range(4):
        assert g.register_failure(db, "ivan", "1.1.1.1", now=T0) is None
    until = g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert until == T0 + timedelta(minutes=10)
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=9)) == until
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=10, seconds=1)) is None


def test_other_ip_not_blocked(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.blocked_until(db, "ivan", "2.2.2.2", now=T0) is None


def test_counter_forgets_after_quiet(db):
    for _ in range(4):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=11)) is None


def test_ip_limit_across_logins(db):
    for i in range(20):
        g.register_failure(db, f"user{i}", "3.3.3.3", now=T0)
    assert g.blocked_until(db, "someone", "3.3.3.3", now=T0) == T0 + timedelta(minutes=10)


def test_ip_counter_forgets_after_quiet(db):
    for i in range(19):
        g.register_failure(db, f"user{i}", "3.3.3.3", now=T0)
    later = T0 + timedelta(minutes=11)
    assert g.register_failure(db, "other", "3.3.3.3", now=later) is None
    assert g.blocked_until(db, "other", "3.3.3.3", now=later) is None


def test_expired_block_starts_from_zero(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    # блок истёк (10 мин): первая новая неудача считается с нуля, а не блокирует сразу
    later = T0 + timedelta(minutes=10, seconds=1)
    assert g.register_failure(db, "ivan", "1.1.1.1", now=later) is None
    assert db.get(LoginAttempt, "ivan|1.1.1.1").failures == 1


def test_failure_during_block_does_not_extend_it(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    until = T0 + timedelta(minutes=10)
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=5)) == until
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0 + timedelta(minutes=5)) == until


def test_success_clears_account_key(db):
    for _ in range(4):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    g.register_success(db, "ivan", "1.1.1.1")
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0) is None


def test_success_keeps_ip_key(db):
    g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    g.register_success(db, "ivan", "1.1.1.1")
    assert db.get(LoginAttempt, "ip|1.1.1.1").failures == 1


def test_unlock(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.unlock(db, "ivan") == 1
    assert g.blocked_until(db, "ivan", "1.1.1.1", now=T0) is None


def test_unlock_underscore_is_not_a_wildcard(db):
    g.register_failure(db, "axb", "1.1.1.1", now=T0)
    g.register_failure(db, "a_b", "1.1.1.1", now=T0)
    assert g.unlock(db, "a_b") == 1
    assert db.get(LoginAttempt, "axb|1.1.1.1") is not None
    assert db.get(LoginAttempt, "a_b|1.1.1.1") is None


def test_reset_all(db):
    g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    g.reset_all(db)
    assert db.query(LoginAttempt).count() == 0


def test_ip_window_is_fixed_not_quiet_period(db):
    # 25 неудач с разных логинов раз в 2 минуты: окно сменяется, старые неудачи не копятся
    for i in range(25):
        assert g.register_failure(db, f"user{i}", "4.4.4.4", now=T0 + timedelta(minutes=2 * i)) is None
    assert g.blocked_until(db, "x", "4.4.4.4", now=T0 + timedelta(minutes=48)) is None
    assert db.get(LoginAttempt, "ip|4.4.4.4").failures <= 6


def test_ip_blocks_at_twenty_within_window(db):
    for i in range(19):
        assert g.register_failure(db, f"user{i}", "5.5.5.5", now=T0 + timedelta(seconds=30 * i)) is None
    last = T0 + timedelta(seconds=30 * 19)  # 9.5 минут от начала окна
    assert g.register_failure(db, "user19", "5.5.5.5", now=last) == last + timedelta(minutes=10)


def test_concurrent_insert_race_is_handled(db, monkeypatch):
    # «чужая» сессия уже вставила строку, а наше чтение ещё видело пустоту -> IntegrityError на вставке
    g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    monkeypatch.setattr(g, "_row_exists", lambda db, key: False)
    assert g.register_failure(db, "ivan", "1.1.1.1", now=T0) is None
    monkeypatch.undo()
    db.expire_all()
    assert db.get(LoginAttempt, "ivan|1.1.1.1").failures == 2
    assert db.get(LoginAttempt, "ip|1.1.1.1").failures == 2


def _burst(n: int, login: str, ip: str) -> list:
    """n потоков с отдельными сессиями одновременно регистрируют неудачу; возвращает результаты."""
    from app.db import SessionLocal

    barrier = threading.Barrier(n)
    results: list = []

    def work():
        with SessionLocal() as session:
            barrier.wait()
            try:
                results.append(g.register_failure(session, login, ip, now=T0))
            except Exception as exc:  # noqa: BLE001 - тест должен увидеть любую ошибку
                results.append(exc)

    threads = [threading.Thread(target=work) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def test_parallel_failures_are_counted_exactly(db):
    results = _burst(4, "ivan", "6.6.6.6")
    assert results == [None] * 4
    assert db.get(LoginAttempt, "ivan|6.6.6.6").failures == 4
    assert db.get(LoginAttempt, "ip|6.6.6.6").failures == 4


def test_parallel_burst_does_not_exceed_limit(db):
    results = _burst(12, "ivan", "7.7.7.7")
    assert not [r for r in results if isinstance(r, Exception)]
    assert results.count(None) == 4  # блок наступает на 5-й неудаче, остальные видят его
    assert results.count(T0 + timedelta(minutes=10)) == 8
    assert db.get(LoginAttempt, "ivan|7.7.7.7").failures == 5  # после блока счётчик не растёт


def test_is_locked_any_ip(db):
    assert g.is_locked(db, "ivan", now=T0) is False
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    assert g.is_locked(db, "ivan", now=T0) is True  # блок на любом IP считается
    assert g.is_locked(db, "ivan", now=T0 + timedelta(minutes=10, seconds=1)) is False
    assert g.is_locked(db, "petr", now=T0) is False


def test_is_locked_ignores_ip_block_and_similar_logins(db):
    for _ in range(20):
        g.register_failure(db, "other", "3.3.3.3", now=T0)  # блок самого IP, не логина
    assert g.is_locked(db, "ivan", now=T0) is False
    for _ in range(5):
        g.register_failure(db, "ivan2", "1.1.1.1", now=T0)
    assert g.is_locked(db, "ivan", now=T0) is False  # «ivan2|...» - другой логин
    for _ in range(5):
        g.register_failure(db, "a_b", "1.1.1.1", now=T0)
    assert g.is_locked(db, "axb", now=T0) is False  # «_» не подстановочный знак


def test_is_locked_cleared_by_unlock(db):
    for _ in range(5):
        g.register_failure(db, "ivan", "1.1.1.1", now=T0)
    g.unlock(db, "ivan")
    assert g.is_locked(db, "ivan", now=T0) is False
