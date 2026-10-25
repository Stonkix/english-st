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
