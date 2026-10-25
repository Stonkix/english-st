from datetime import timedelta

import pytest
from fastapi import APIRouter, Depends, Response
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.auth import deps
from app.auth import sessions as s
from app.main import app
from app.models import Session as SessionRow
from app.timeutil import utcnow
from tests.factories import make_user


def test_create_and_resolve(db):
    u = make_user(db)
    tok = s.create_session(db, u, "1.1.1.1", "ua")
    assert s.get_session_user(db, tok).id == u.id
    assert s.get_session_user(db, "wrong") is None and s.get_session_user(db, None) is None


def test_token_stored_only_as_hash(db):
    u = make_user(db)
    tok = s.create_session(db, u, "1.1.1.1", "ua")
    row = db.scalars(select(SessionRow)).one()
    assert row.token_hash != tok and len(row.token_hash) == 64
    assert (row.ip, row.user_agent, row.user_id) == ("1.1.1.1", "ua", u.id)


def test_expiry_and_sliding(db):
    u = make_user(db)
    now = utcnow()
    tok = s.create_session(db, u, "ip", "ua", now=now)
    assert s.get_session_user(db, tok, now=now + timedelta(days=29)) is not None  # продлилась
    assert s.get_session_user(db, tok, now=now + timedelta(days=29 + 29)) is not None
    assert s.get_session_user(db, tok, now=now + timedelta(days=29 + 29 + 31)) is None


def test_expired_session_row_is_deleted(db):
    u = make_user(db)
    now = utcnow()
    tok = s.create_session(db, u, "ip", "ua", now=now)
    assert s.get_session_user(db, tok, now=now + timedelta(days=31)) is None
    assert db.scalars(select(SessionRow)).all() == []


def test_touch_not_more_often_than_hourly(db):
    u = make_user(db)
    now = utcnow()
    tok = s.create_session(db, u, "ip", "ua", now=now)
    row = db.scalars(select(SessionRow)).one()
    s.get_session_user(db, tok, now=now + timedelta(minutes=30))
    assert row.last_seen_at == now and row.expires_at == now + s.SESSION_TTL  # не трогали
    later = now + timedelta(hours=1)
    s.get_session_user(db, tok, now=later)
    assert row.last_seen_at == later and row.expires_at == later + s.SESSION_TTL


def test_inactive_user_rejected(db):
    u = make_user(db)
    tok = s.create_session(db, u, "ip", "ua")
    u.is_active = False
    db.commit()
    assert s.get_session_user(db, tok) is None


def test_revoke_session(db):
    u = make_user(db)
    tok = s.create_session(db, u, "ip", "ua")
    s.revoke_session(db, tok)
    assert s.get_session_user(db, tok) is None


def test_revoke_all_but_current(db):
    u = make_user(db)
    a = s.create_session(db, u, "ip", "ua")
    b = s.create_session(db, u, "ip", "ua")
    assert s.revoke_user_sessions(db, u.id, keep_token=a) == 1
    assert s.get_session_user(db, a) and s.get_session_user(db, b) is None


def test_revoke_all_without_keep(db):
    u = make_user(db)
    other = make_user(db)
    s.create_session(db, u, "ip", "ua")
    s.create_session(db, u, "ip", "ua")
    keep = s.create_session(db, other, "ip", "ua")
    assert s.revoke_user_sessions(db, u.id) == 2
    assert s.get_session_user(db, keep) is not None  # чужие сессии не тронуты


def test_cookie_flags():
    r = Response()
    s.set_session_cookie(r, "tok")
    c = r.headers["set-cookie"].lower()
    assert "est_session=tok" in c and "httponly" in c and "samesite=lax" in c
    assert "path=/" in c and f"max-age={30 * 24 * 3600}" in c
    assert "secure" not in c  # в тестах DEBUG=true


def test_cookie_secure_in_production(monkeypatch):
    monkeypatch.setattr("app.auth.sessions.settings.debug", False)
    r = Response()
    s.set_session_cookie(r, "tok")
    assert "secure" in r.headers["set-cookie"].lower()


def test_clear_cookie():
    r = Response()
    s.clear_session_cookie(r)
    c = r.headers["set-cookie"].lower()
    assert "est_session=" in c and "max-age=0" in c


# ---------- Origin ----------

def test_post_foreign_or_missing_origin_forbidden(client):
    assert client.post("/logout", headers={"Origin": "https://evil.example"}).status_code == 403
    assert TestClient(app).post("/logout").status_code == 403  # ни Origin, ни Referer


@pytest.mark.parametrize("origin", ["null", "not a url", "http://testserver:8000", "https://testserver"])
def test_bad_origin_forbidden(client, origin):
    assert client.post("/nope", headers={"Origin": origin}).status_code == 403


def test_matching_origin_passes_middleware(client):
    # дошли до роутера: маршрута нет → 404/405, а не 403
    assert client.post("/nope").status_code in (404, 405)


def test_referer_used_when_origin_missing():
    with TestClient(app) as c:
        ok = c.post("/nope", headers={"Referer": "http://testserver/some/page?x=1"})
        assert ok.status_code in (404, 405)
        bad = c.post("/nope", headers={"Referer": "https://evil.example/page"})
        assert bad.status_code == 403


def test_origin_wins_over_referer():
    with TestClient(app) as c:
        r = c.post("/nope", headers={"Origin": "https://evil.example", "Referer": "http://testserver/"})
        assert r.status_code == 403


def test_get_ignores_origin():
    with TestClient(app) as c:
        assert c.get("/healthz", headers={"Origin": "https://evil.example"}).status_code == 200


def test_base_url_read_at_request_time(monkeypatch):
    monkeypatch.setattr("app.main.settings.base_url", "https://school.example")
    with TestClient(app) as c:
        assert c.post("/nope", headers={"Origin": "https://school.example"}).status_code in (404, 405)
        assert c.post("/nope", headers={"Origin": "http://testserver"}).status_code == 403


def test_default_port_equivalence(monkeypatch):
    monkeypatch.setattr("app.main.settings.base_url", "https://school.example")
    with TestClient(app) as c:
        r = c.post("/nope", headers={"Origin": "https://school.example:443"})
        assert r.status_code in (404, 405)


# ---------- зависимости ----------

@pytest.fixture
def probe():
    """Временный роутер с проверочными маршрутами; из приложения убирается после теста."""
    router = APIRouter()

    @router.get("/_t/user")
    def t_user(user=Depends(deps.require_user)):
        return {"login": user.login}

    @router.get("/_t/teacher")
    def t_teacher(user=Depends(deps.require_role("teacher"))):
        return {"role": user.role}

    @router.get("/_t/admin")
    def t_admin(user=Depends(deps.require_role("admin"))):
        return {"role": user.role}

    # настоящий /password/new уже есть в приложении; настоящий /logout — только POST, а здесь GET
    @router.get("/logout")
    def t_logout(user=Depends(deps.require_user)):
        return {"ok": True}

    @router.get("/_t/maybe")
    def t_maybe(user=Depends(deps.get_current_user)):
        return {"login": user.login if user else None}

    @router.get("/_t/bye")
    def t_bye(user=Depends(deps.require_user)):
        response = Response("bye")
        s.clear_session_cookie(response)
        return response

    before = len(app.router.routes)
    app.include_router(router)
    added = app.router.routes[before:]
    yield
    for route in added:
        app.router.routes.remove(route)


def login_cookie(client, db, **kwargs):
    user = make_user(db, **kwargs)
    client.cookies.set(s.COOKIE_NAME, s.create_session(db, user, "ip", "ua"))
    return user


def test_anonymous_redirected_to_login(client, probe):
    r = client.get("/_t/user", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_anonymous_htmx_gets_hx_redirect(client, probe):
    r = client.get("/_t/user", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200 and r.headers["hx-redirect"] == "/login"
    assert "location" not in r.headers


def test_get_current_user_optional(client, db, probe):
    assert client.get("/_t/maybe").json() == {"login": None}
    user = login_cookie(client, db)
    assert client.get("/_t/maybe").json() == {"login": user.login}


def test_require_user_admits_logged_in(client, db, probe):
    user = login_cookie(client, db)
    assert client.get("/_t/user").json() == {"login": user.login}


def test_deactivated_user_kicked_to_login(client, db, probe):
    user = login_cookie(client, db)
    assert client.get("/_t/user").status_code == 200
    user.is_active = False
    db.commit()
    r = client.get("/_t/user", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_require_role_teacher(client, db, probe):
    login_cookie(client, db, role="teacher")
    assert client.get("/_t/teacher").status_code == 200


def test_admin_passes_teacher_check(client, db, probe):
    login_cookie(client, db, role="admin")
    assert client.get("/_t/teacher").status_code == 200
    assert client.get("/_t/admin").status_code == 200


def test_teacher_cannot_pass_admin_check(client, db, probe):
    login_cookie(client, db, role="teacher")
    assert client.get("/_t/admin").status_code == 403


def test_student_forbidden_for_teacher_route(client, db, probe):
    login_cookie(client, db, role="student")
    assert client.get("/_t/teacher").status_code == 403


def test_role_check_anonymous_redirects(client, probe):
    assert client.get("/_t/teacher", follow_redirects=False).status_code == 303


def test_must_change_password_redirects(client, db, probe):
    login_cookie(client, db, must_change=True)
    r = client.get("/_t/user", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/password/new"
    r = client.get("/_t/user", headers={"HX-Request": "true"}, follow_redirects=False)
    assert r.status_code == 200 and r.headers["hx-redirect"] == "/password/new"


def test_must_change_password_allowlist(client, db, probe):
    login_cookie(client, db, must_change=True)
    assert client.get("/password/new", follow_redirects=False).status_code == 200
    assert client.get("/logout", follow_redirects=False).status_code == 200


def test_must_change_password_before_role_check(client, db, probe):
    login_cookie(client, db, role="teacher", must_change=True)
    r = client.get("/_t/teacher", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/password/new"


def test_home_for(db):
    assert deps.home_for(make_user(db, role="student")) == "/"
    assert deps.home_for(make_user(db, role="teacher")) == "/teacher"
    assert deps.home_for(make_user(db, role="admin")) == "/teacher"


# ---------- скольжение cookie ----------

def age_session(db, hours):
    """Делает вид, что сессию в последний раз продлевали hours часов назад."""
    row = db.scalars(select(SessionRow)).one()
    row.last_seen_at = utcnow() - timedelta(hours=hours)
    db.commit()


def test_cookie_reissued_after_touch(client, db, probe):
    login_cookie(client, db)
    token = client.cookies.get(s.COOKIE_NAME)
    age_session(db, hours=2)
    r = client.get("/_t/user")
    assert r.status_code == 200
    c = r.headers["set-cookie"].lower()
    assert f"est_session={token}".lower() in c and f"max-age={30 * 24 * 3600}" in c and "httponly" in c


def test_cookie_not_reissued_within_the_hour(client, db, probe):
    login_cookie(client, db)
    age_session(db, hours=0)
    r = client.get("/_t/user")
    assert r.status_code == 200 and "set-cookie" not in r.headers


def test_cookie_reissued_once_per_hour(client, db, probe):
    login_cookie(client, db)
    age_session(db, hours=2)
    assert "set-cookie" in client.get("/_t/user").headers
    assert "set-cookie" not in client.get("/_t/user").headers  # только что продлили


def test_clearing_cookie_wins_over_refresh(client, db, probe):
    login_cookie(client, db)
    age_session(db, hours=2)
    r = client.get("/_t/bye")
    cookies = r.headers.get_list("set-cookie")
    assert len(cookies) == 1 and "max-age=0" in cookies[0].lower()
