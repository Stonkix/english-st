from fastapi.testclient import TestClient
from sqlalchemy import select

from app.main import app
from app.models import LoginAttempt, Session as SessionRow
from tests.factories import login_as, make_user


def test_login_success_sets_cookie(client, db):
    make_user(db, login="ivan")
    r = login_as(client, " IVAN ")
    assert r.status_code == 303 and r.headers["location"] == "/"
    c = r.headers["set-cookie"]
    assert "est_session=" in c and "HttpOnly" in c and "samesite=lax" in c.lower()


def test_wrong_password_generic(client, db):
    make_user(db, login="ivan")
    r = login_as(client, "ivan", "Wrong12345a")
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text
    assert "Неверный логин или пароль" in login_as(client, "nobody", "x").text


def test_lockout_case_insensitive(client, db):
    make_user(db, login="ivan")
    for name in ["ivan", "IVAN", "Ivan", "iVan", "ivaN"]:
        login_as(client, name, "Wrong12345a")
    r = login_as(client, "ivan")  # верный пароль, но блок
    assert r.status_code == 429 and "Попробуйте через 10 мин" in r.text


def test_layout_hint(client, db):
    make_user(db, login="ivan")
    assert "Проверьте раскладку" in login_as(client, "ivan", "Пароль12345").text


def test_must_change_flow(client, db):
    make_user(db, login="kid", must_change=True)
    assert login_as(client, "kid").headers["location"] == "/password/new"
    assert client.get("/", follow_redirects=False).headers["location"] == "/password/new"
    r = client.post("/password/new", data={"new": "short", "repeat": "short"})
    assert "Не короче 10 символов" in r.text
    r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    client.post("/logout")
    assert login_as(client, "kid", "NewPassw0rd").headers["location"] == "/"


def test_teacher_home_redirect(client, db):
    make_user(db, role="teacher", login="anna")
    login_as(client, "anna")
    assert client.get("/", follow_redirects=False).headers["location"] == "/teacher"


def test_anonymous_redirected(client):
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


# ---------- вход: крайние случаи ----------

def test_inactive_user_same_as_wrong_password(client, db):
    make_user(db, login="ivan", is_active=False)
    r = login_as(client, "ivan")  # пароль верный
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text
    assert "est_session" not in r.headers.get("set-cookie", "")
    assert db.scalars(select(LoginAttempt).where(LoginAttempt.key == "ivan|testclient")).one().failures == 1


def test_unknown_and_inactive_login_still_verify_password(client, db, monkeypatch):
    """Время ответа не должно выдавать, есть ли такой логин: хеш сверяется всегда."""
    calls = []
    import app.routes.auth as mod

    original = mod.verify_password
    monkeypatch.setattr(mod, "verify_password", lambda p, h: calls.append(h) or original(p, h))
    make_user(db, login="off", is_active=False)
    login_as(client, "nobody")
    login_as(client, "off")
    assert len(calls) == 2 and calls[0] == mod._DUMMY_HASH


def test_blocked_login_skips_password_check(client, db, monkeypatch):
    make_user(db, login="ivan")
    for _ in range(5):
        login_as(client, "ivan", "Wrong12345a")
    import app.routes.auth as mod

    def boom(*a):
        raise AssertionError("пароль не должен проверяться при блоке")

    monkeypatch.setattr(mod, "verify_password", boom)
    assert login_as(client, "ivan").status_code == 429


def test_very_long_password_is_just_wrong(client, db):
    make_user(db, login="ivan")
    r = login_as(client, "ivan", "Aa1" * 200)
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_unencodable_password_is_just_wrong(client, db):
    make_user(db, login="ivan")
    r = client.post(
        "/login",
        content=b"login=ivan&password=%ED%A0%80Passw0rd12",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        follow_redirects=False,
    )
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_verify_error_is_just_wrong(client, db, monkeypatch):
    make_user(db, login="ivan")
    import app.routes.auth as mod

    def boom(*a):
        raise UnicodeEncodeError("utf-8", "x", 0, 1, "surrogate")

    monkeypatch.setattr(mod, "verify_password", boom)
    r = login_as(client, "ivan")
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_guard_key_is_truncated(client, db):
    login_as(client, "a" * 500, "x")
    keys = db.scalars(select(LoginAttempt.key)).all()
    assert f"{'a' * 32}|testclient" in keys and all(len(k) < 60 for k in keys)


def test_long_login_does_not_match_32_char_account(client, db):
    make_user(db, login="a" * 32)
    r = login_as(client, "a" * 32 + "b")
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_blocked_message_rounds_minutes_up(client, db):
    make_user(db, login="ivan")
    for _ in range(5):
        login_as(client, "ivan", "Wrong12345a")
    row = db.scalars(select(LoginAttempt).where(LoginAttempt.key == "ivan|testclient")).one()
    from datetime import timedelta
    from app.timeutil import utcnow

    row.blocked_until = utcnow() + timedelta(minutes=2, seconds=5)
    db.commit()
    assert "через 3 мин" in login_as(client, "ivan").text


def test_layout_hint_only_for_non_latin_failure(client, db):
    make_user(db, login="ivan")
    assert "Проверьте раскладку" not in login_as(client, "ivan", "Wrong12345a").text
    assert "Проверьте раскладку" not in login_as(client, "nobody", "x").text


def test_login_page_keeps_login_not_password(client, db):
    make_user(db, login="ivan")
    r = login_as(client, "<ivan>", "Secret12345x")
    assert 'value="&lt;ivan&gt;"' in r.text and "Secret12345x" not in r.text


def test_login_page_when_logged_in_redirects_home(client, db):
    make_user(db, login="ivan")
    login_as(client, "ivan")
    r = client.get("/login", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    make_user(db, role="teacher", login="anna")
    client.post("/logout")
    login_as(client, "anna")
    assert client.get("/login", follow_redirects=False).headers["location"] == "/teacher"


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200 and 'name="login"' in r.text and 'name="password"' in r.text
    assert "Забыли пароль? Попросите учителя его сбросить" in r.text
    assert "<nav" not in r.text


def test_relogin_revokes_previous_session(client, db):
    make_user(db, login="ivan")
    login_as(client, "ivan")
    login_as(client, "ivan")
    assert len(db.scalars(select(SessionRow)).all()) == 1


# ---------- выход ----------

def test_logout_revokes_session_and_clears_cookie(client, db):
    make_user(db, login="ivan")
    login_as(client, "ivan")
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert db.scalars(select(SessionRow)).all() == []
    assert client.get("/", follow_redirects=False).headers["location"] == "/login"


def test_logout_without_session(client):
    r = client.post("/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"
    assert "max-age=0" in r.headers["set-cookie"].lower()


# ---------- смена пароля ----------

def test_password_new_requires_login(client):
    assert client.get("/password/new", follow_redirects=False).headers["location"] == "/login"
    r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd"}, follow_redirects=False)
    assert r.headers["location"] == "/login"


def test_password_new_mismatch(client, db):
    make_user(db, login="kid", must_change=True)
    login_as(client, "kid")
    r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd1"})
    assert r.status_code == 200 and "Пароли не совпадают" in r.text
    assert db.scalars(select(SessionRow)).one()  # сессия жива, флаг не снят
    assert client.get("/", follow_redirects=False).headers["location"] == "/password/new"


def test_password_new_shows_all_broken_rules(client, db):
    make_user(db, login="kid", must_change=True)
    login_as(client, "kid")
    r = client.post("/password/new", data={"new": "пароль", "repeat": "пароль"})
    for text in ("Не короче 10 символов", "строчная латинская", "заглавная латинская", "Есть цифра", "без русских букв"):
        assert text in r.text


def test_password_new_too_long(client, db):
    make_user(db, login="kid", must_change=True)
    login_as(client, "kid")
    long = "Aa1" + "b" * 300
    r = client.post("/password/new", data={"new": long, "repeat": long})
    assert r.status_code == 200 and "Не длиннее 256 символов" in r.text


def test_password_new_page_has_rules_and_toggle(client, db):
    make_user(db, login="kid", must_change=True)
    login_as(client, "kid")
    r = client.get("/password/new")
    assert r.status_code == 200
    for code in ("length", "lower", "upper", "digit", "charset"):
        assert f'data-rule="{code}"' in r.text
    assert "data-toggle-password" in r.text and "<nav" not in r.text  # на принудительной смене меню нет
    assert "Выйти" in r.text


def test_password_new_only_for_forced_change(client, db):
    """Без текущего пароля менять его нельзя: обычный пользователь уходит на главную (смена — в профиле)."""
    user = make_user(db, login="ivan")
    old_hash = user.password_hash
    login_as(client, "ivan")
    r = client.get("/password/new", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    db.refresh(user)
    assert user.password_hash == old_hash
    make_user(db, role="teacher", login="anna")
    client.post("/logout")
    login_as(client, "anna")
    assert client.get("/password/new", follow_redirects=False).headers["location"] == "/teacher"


def test_password_change_revokes_other_sessions(client, db):
    make_user(db, login="kid", must_change=True)
    login_as(client, "kid")
    with TestClient(app, headers={"Origin": "http://testserver"}) as other:  # «другое устройство»
        assert login_as(other, "kid").headers["location"] == "/password/new"
        r = client.post("/password/new", data={"new": "NewPassw0rd", "repeat": "NewPassw0rd"}, follow_redirects=False)
        assert r.status_code == 303
        assert other.get("/password/new", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/", follow_redirects=False).status_code == 200  # текущая сессия жива
    assert len(db.scalars(select(SessionRow)).all()) == 1


# ---------- главная ----------

def test_student_home_greets_by_name(client, db):
    make_user(db, login="ivan", first_name="Маша <b>")
    login_as(client, "ivan")
    r = client.get("/")
    assert r.status_code == 200
    assert "Привет, Маша &lt;b&gt;! Скоро здесь появятся ежедневные задания." in r.text
    assert "<nav" in r.text


# ---------- страницы ошибок ----------

def test_error_pages_keep_nav_for_logged_in(client, db):
    make_user(db, login="ivan")
    login_as(client, "ivan")
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "Страница не найдена" in r.text and "<nav" in r.text


def test_error_pages_anonymous_have_no_nav(client):
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "<nav" not in r.text


def test_403_page_keeps_nav_for_logged_in(client, db):
    from fastapi import APIRouter, Depends

    from app.auth import deps
    from app.main import app

    router = APIRouter()

    @router.get("/_t/only-teacher")
    def only_teacher(user=Depends(deps.require_role("teacher"))):
        return {}

    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    try:
        make_user(db, login="ivan")
        login_as(client, "ivan")
        r = client.get("/_t/only-teacher")
        assert r.status_code == 403 and "<nav" in r.text
    finally:
        for route in added:
            app.router.routes.remove(route)
