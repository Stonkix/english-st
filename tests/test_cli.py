import re

import pytest

from app.auth.login_guard import register_failure
from app.auth.passwords import verify_password
from app.auth.sessions import create_session
from app.cli import main
from app.models import LoginAttempt, Session, User
from tests.factories import make_user


def _temp_password(out: str) -> str:
    return re.search(r"Временный пароль: (\S+)", out).group(1)


def test_create_admin(db, capsys):
    assert main(["create-admin", "--login", "Boss", "--first-name", "Ольга", "--last-name", "Иванова"]) == 0
    pwd = _temp_password(capsys.readouterr().out)
    u = db.query(User).filter_by(login="boss").one()
    assert u.role == "admin" and u.must_change_password and verify_password(pwd, u.password_hash)
    assert main(["create-admin", "--login", "boss", "--first-name", "О", "--last-name", "И"]) == 1


def test_create_admin_rejects_invalid_login(db, capsys):
    assert main(["create-admin", "--login", "Бо", "--first-name", "Ольга", "--last-name", "Иванова"]) == 1
    assert "Ошибка" in capsys.readouterr().err
    assert db.query(User).count() == 0


def test_create_admin_rejects_empty_name(db, capsys):
    assert main(["create-admin", "--login", "boss", "--first-name", " ", "--last-name", "Иванова"]) == 1
    assert "Ошибка" in capsys.readouterr().err
    assert db.query(User).count() == 0


def test_reset_password(db, capsys):
    make_user(db, login="kid")
    assert main(["reset-password", "--login", "kid"]) == 0
    pwd = _temp_password(capsys.readouterr().out)
    db.expire_all()
    u = db.query(User).filter_by(login="kid").one()
    assert verify_password(pwd, u.password_hash) and u.must_change_password


def test_reset_password_revokes_sessions(db, capsys):
    user = make_user(db, login="kid")
    create_session(db, user, ip="1.2.3.4", user_agent="test")
    create_session(db, user, ip="5.6.7.8", user_agent="test")
    assert main(["reset-password", "--login", "KID"]) == 0
    assert db.query(Session).filter_by(user_id=user.id).count() == 0


def test_reset_password_unknown_login(db, capsys):
    assert main(["reset-password", "--login", "nobody"]) == 1
    assert "не найден" in capsys.readouterr().err


def test_unlock_by_login(db, capsys):
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "other", "1.2.3.4")
    assert main(["unlock", "--login", "KID"]) == 0
    assert "Снято блокировок: 1" in capsys.readouterr().out
    keys = {row.key for row in db.query(LoginAttempt).all()}
    assert keys == {"other|1.2.3.4", "ip|1.2.3.4"}


def test_unlock_all(db, capsys):
    register_failure(db, "kid", "1.2.3.4")
    register_failure(db, "other", "5.6.7.8")
    assert main(["unlock", "--all"]) == 0
    assert "Снято блокировок: 4" in capsys.readouterr().out
    assert db.query(LoginAttempt).count() == 0


def test_unlock_needs_login_or_all(db):
    with pytest.raises(SystemExit):
        main(["unlock"])


def test_unlock_login_and_all_are_exclusive(db):
    with pytest.raises(SystemExit):
        main(["unlock", "--login", "kid", "--all"])
