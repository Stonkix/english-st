from app.auth.sessions import COOKIE_NAME
from app.models import Group, Session, User
from tests.factories import login_as, make_group, make_user


def admin(client, db):
    boss = make_user(db, role="admin", login="boss")
    login_as(client, "boss")
    return boss


def test_admin_creates_teacher(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    r = client.post("/school/teachers", data={"first_name": "Анна", "last_name": "Смирнова", "login": "anna"})
    assert "больше он не покажется" in r.text
    assert r.headers["cache-control"] == "no-store"
    t = db.query(User).filter_by(login="anna").one()
    assert t.role == "teacher" and t.must_change_password


def test_teacher_cannot_open_school(client, db):
    make_user(db, role="teacher", login="teach1"); login_as(client, "teach1")
    assert client.get("/school").status_code == 403
    assert client.get("/school/teachers").status_code == 403
    assert client.get("/admin/", follow_redirects=False).status_code in (302, 303)


def test_admin_has_teacher_powers(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    assert client.get("/teacher").status_code == 200
    r = client.post("/teacher/groups", data={"name": "Моя группа"}, follow_redirects=True)
    assert "Моя группа" in r.text


def test_admin_nav_has_school(client, db):
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    assert 'href="/school"' in client.get("/teacher").text


def test_school_index_and_list(client, db):
    admin(client, db)
    make_user(db, role="teacher", login="teach1", first_name="Анна", last_name="Смирнова")
    assert "Учителя" in client.get("/school").text
    page = client.get("/school/teachers").text
    assert "teach1" in page and "Анна Смирнова" in page


def test_create_teacher_validation(client, db):
    admin(client, db)
    make_user(db, role="student", login="taken")
    r = client.post("/school/teachers", data={"first_name": "А", "last_name": "Б", "login": "Пе"})
    assert "Логин: латинские буквы" in r.text
    r = client.post("/school/teachers", data={"first_name": " ", "last_name": "Б", "login": "okok"})
    assert "Введите имя и фамилию" in r.text
    r = client.post("/school/teachers", data={"first_name": "А", "last_name": "Б", "login": "taken"})
    assert "Логин уже занят" in r.text
    assert db.query(User).filter_by(role="teacher").count() == 0


def test_reset_password_revokes_sessions(client, db):
    admin(client, db)
    t = make_user(db, role="teacher", login="teach1")
    other = type(client)(client.app, headers={"Origin": "http://testserver"})
    login_as(other, "teach1")
    assert db.query(Session).filter_by(user_id=t.id).count() == 1
    r = client.post(f"/school/teachers/{t.id}/reset-password")
    assert "больше он не покажется" in r.text and r.headers["cache-control"] == "no-store"
    db.expire_all()
    assert db.query(Session).filter_by(user_id=t.id).count() == 0
    assert db.get(User, t.id).must_change_password


def test_deactivate_and_activate_teacher(client, db):
    admin(client, db)
    t = make_user(db, role="teacher", login="teach1")
    g = make_group(db, t)
    login_as(type(client)(client.app, headers={"Origin": "http://testserver"}), "teach1")
    assert client.post(f"/school/teachers/{t.id}/deactivate", follow_redirects=False).status_code == 303
    db.expire_all()
    assert not db.get(User, t.id).is_active
    assert db.query(Session).filter_by(user_id=t.id).count() == 0
    assert db.get(Group, g.id).teacher_id == t.id
    assert client.post(f"/school/teachers/{t.id}/activate", follow_redirects=False).status_code == 303
    db.expire_all()
    assert db.get(User, t.id).is_active


def test_teacher_routes_only_for_teachers(client, db):
    admin(client, db)
    s = make_user(db, role="student", login="kid1")
    other_admin = make_user(db, role="admin", login="boss2")
    for u in (s, other_admin):
        for action in ("reset-password", "deactivate", "activate"):
            assert client.post(f"/school/teachers/{u.id}/{action}").status_code == 404
    assert client.post("/school/teachers/9999/deactivate").status_code == 404
    db.expire_all()
    assert db.get(User, other_admin.id).is_active


def test_teacher_cannot_use_school_actions(client, db):
    t = make_user(db, role="teacher", login="teach1"); login_as(client, "teach1")
    assert client.post(f"/school/teachers/{t.id}/deactivate").status_code == 403
    assert client.post("/school/teachers", data={"first_name": "А", "last_name": "Б", "login": "newone"}).status_code == 403


def test_sqladmin_requires_admin_session(client, db):
    r = client.get("/admin/", follow_redirects=False)
    assert r.status_code in (302, 303) and r.headers["location"].endswith("/login")
    make_user(db, role="admin", login="boss"); login_as(client, "boss")
    assert client.get("/admin/", follow_redirects=False).status_code == 200


def test_sqladmin_hides_password_hash_and_create(client, db):
    admin(client, db)
    u = make_user(db, role="student", login="kid1")
    pages = {
        "list": client.get("/admin/user/list"),
        "details": client.get(f"/admin/user/details/{u.id}"),
        "edit": client.get(f"/admin/user/edit/{u.id}"),
        "csv": client.get("/admin/user/export/csv"),
        "json": client.get("/admin/user/export/json"),
    }
    for name, r in pages.items():
        assert r.status_code == 200, name
        assert "kid1" in r.text, name  # страница с данными, а не пустая
        assert u.password_hash not in r.text, name
        assert "scrypt$" not in r.text and "password_hash" not in r.text, name
    assert client.get("/admin/user/create", follow_redirects=False).status_code in (302, 303, 403)
    assert client.get("/admin/group/list").status_code == 200


def test_sqladmin_rejects_deactivated_admin_cookie(client, db):
    boss = admin(client, db)
    boss.is_active = False
    db.commit()
    assert client.get("/admin/", follow_redirects=False).status_code in (302, 303)
    assert COOKIE_NAME in client.cookies
