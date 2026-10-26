import re

from fastapi.testclient import TestClient

from app.auth import login_guard
from app.main import app
from app.models import Group, User
from tests.factories import login_as, make_group, make_user, seed_filter


def setup_teacher(client, db, login="teach1"):
    t = make_user(db, role="teacher", login=login)
    g = make_group(db, t)
    login_as(client, login)
    return t, g


def new_student(client, g, **over):
    data = {"first_name": "Петя", "last_name": "Котов", "login": "petya.kotov", **over}
    return client.post(f"/teacher/groups/{g.id}/students", data=data)


def kid_client(login="kid"):
    kid = TestClient(app, headers={"Origin": "http://testserver"})
    login_as(kid, login)
    return kid


# ---------- ученики: создание ----------


def test_create_student_shows_temp_password_once(client, db):
    t, g = setup_teacher(client, db)
    r = client.post(
        f"/teacher/groups/{g.id}/students",
        data={"first_name": "Петя", "last_name": "Котов", "login": "petya.kotov"},
    )
    assert r.status_code == 200 and "больше он не покажется" in r.text
    pwd = re.search(r'data-temp-password="([^"]+)"', r.text).group(1)
    s = db.query(User).filter_by(login="petya.kotov").one()
    assert s.group_id == g.id and s.must_change_password and s.role == "student"
    client.post("/logout")
    assert login_as(client, "petya.kotov", pwd).headers["location"] == "/password/new"


def test_temp_password_page_is_not_cached(client, db):
    t, g = setup_teacher(client, db)
    assert new_student(client, g).headers["cache-control"] == "no-store"


def test_login_is_normalized_on_create(client, db):
    t, g = setup_teacher(client, db)
    new_student(client, g, login="  Petya.Kotov ")
    assert db.query(User).filter_by(login="petya.kotov").count() == 1


def test_duplicate_login_friendly(client, db):
    t, g = setup_teacher(client, db)
    data = {"first_name": "Петя", "last_name": "Котов", "login": "petya"}
    client.post(f"/teacher/groups/{g.id}/students", data=data)
    r = client.post(f"/teacher/groups/{g.id}/students", data=data)
    assert r.status_code == 200 and "Логин уже занят" in r.text
    assert db.query(User).filter_by(login="petya").count() == 1


def test_duplicate_login_race_is_friendly(client, db, monkeypatch):
    """Двойное нажатие: проверка занятости прошла, а вставка упёрлась в уникальный индекс."""
    t, g = setup_teacher(client, db)
    make_user(db, login="petya")
    from app.routes import teacher

    monkeypatch.setattr(teacher, "login_taken", lambda db, login: False)
    r = new_student(client, g, login="petya")
    assert r.status_code == 200 and "Логин уже занят" in r.text
    assert db.query(User).filter_by(login="petya").count() == 1


def test_create_student_validation(client, db):
    t, g = setup_teacher(client, db)
    r = new_student(client, g, login="Пе")
    assert "Логин: латинские буквы, цифры, точка, дефис, подчёркивание; от 3 до 32 символов" in r.text
    r = new_student(client, g, first_name="  ", login="ok.login")
    assert "Введите имя и фамилию" in r.text
    r = new_student(client, g, last_name="К" * 51, login="ok.login")
    assert "Имя и фамилия — не длиннее 50 символов" in r.text
    assert db.query(User).filter_by(role="student").count() == 0
    # введённое не пропадает
    assert 'value="Петя"' in r.text


def test_create_student_in_foreign_group_404(client, db):
    other = make_user(db, role="teacher", login="teach2")
    g2 = make_group(db, other)
    setup_teacher(client, db)
    assert new_student(client, g2).status_code == 404
    assert client.get(f"/teacher/groups/{g2.id}/students/new").status_code == 404
    assert db.query(User).filter_by(login="petya.kotov").count() == 0


def test_login_suggest(client, db):
    t, g = setup_teacher(client, db)
    make_user(db, login="petya.kotov")
    r = client.get("/teacher/login-suggest", params={"first_name": "Петя", "last_name": "Котов"})
    assert r.text == "petya.kotov2"
    # порядок параметров в запросе не важен (так уходит второй запрос, когда меняют фамилию)
    r = client.get("/teacher/login-suggest", params={"last_name": "Котов", "first_name": "Вася"})
    assert r.text == "vasya.kotov"


def test_login_suggest_empty_when_nothing_usable(client, db):
    setup_teacher(client, db)
    r = client.get("/teacher/login-suggest", params={"first_name": "Я", "last_name": ""})  # «ya» — короче 3
    assert r.status_code == 200 and r.text == ""
    r = client.get("/teacher/login-suggest", params={"first_name": "!!", "last_name": "??"})
    assert r.text == ""


def test_login_suggest_needs_teacher(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    assert client.get("/teacher/login-suggest").status_code == 403


# ---------- доступ ----------


def test_foreign_group_404(client, db):
    other = make_user(db, role="teacher", login="teach2")
    g2 = make_group(db, other)
    s2 = make_user(db, login="s2x", group=g2)
    setup_teacher(client, db)
    assert client.get(f"/teacher/groups/{g2.id}").status_code == 404
    assert client.post(f"/teacher/students/{s2.id}/reset-password").status_code == 404
    assert client.post(f"/teacher/groups/{g2.id}/rename", data={"name": "Моя"}).status_code == 404
    db.refresh(g2)
    assert g2.name == "Группа 1"


def test_student_cannot_open_teacher(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    assert client.get("/teacher").status_code == 403


def test_teacher_account_is_not_a_student_target(client, db):
    t, g = setup_teacher(client, db)
    colleague = make_user(db, role="teacher", login="teach2", group=g)  # даже если вдруг привязан к группе
    assert client.get(f"/teacher/students/{colleague.id}").status_code == 404
    assert client.post(f"/teacher/students/{colleague.id}/reset-password").status_code == 404
    assert client.post(f"/teacher/students/{t.id}/deactivate").status_code == 404
    assert client.get("/teacher/students/99999").status_code == 404


def test_admin_sees_all_groups_and_students(client, db):
    teacher = make_user(db, role="teacher", login="teach2", first_name="Анна", last_name="Смирнова")
    g = make_group(db, teacher, name="Ракеты")
    s = make_user(db, login="kid", group=g)
    make_user(db, role="admin", login="boss")
    login_as(client, "boss")
    r = client.get("/teacher")
    assert "Ракеты" in r.text and "Анна Смирнова" in r.text
    assert client.get(f"/teacher/groups/{g.id}").status_code == 200
    assert client.get(f"/teacher/students/{s.id}").status_code == 200


# ---------- группы ----------


def test_groups_list_shows_only_own(client, db):
    other = make_user(db, role="teacher", login="teach2")
    make_group(db, other, name="Чужая")
    setup_teacher(client, db)
    r = client.get("/teacher")
    assert "Группа 1" in r.text and "Чужая" not in r.text


def test_create_group(client, db):
    t, g = setup_teacher(client, db)
    r = client.post("/teacher/groups", data={"name": "  Звёздочки "}, follow_redirects=False)
    assert r.status_code == 303
    new = db.query(Group).filter_by(name="Звёздочки").one()
    assert new.teacher_id == t.id
    assert r.headers["location"] == f"/teacher/groups/{new.id}"


def test_create_group_requires_name(client, db):
    setup_teacher(client, db)
    r = client.post("/teacher/groups", data={"name": "   "})
    assert r.status_code == 200 and "Введите название группы" in r.text
    assert db.query(Group).count() == 1


def test_rename_group(client, db):
    t, g = setup_teacher(client, db)
    r = client.post(f"/teacher/groups/{g.id}/rename", data={"name": " Ракеты "}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(g)
    assert g.name == "Ракеты"
    r = client.post(f"/teacher/groups/{g.id}/rename", data={"name": ""})
    assert "Введите название группы" in r.text
    db.refresh(g)
    assert g.name == "Ракеты"


def test_group_page_badges(client, db):
    t, g = setup_teacher(client, db)
    make_user(db, login="okkid", group=g, first_name="Аня")
    make_user(db, login="offkid", group=g, is_active=False, first_name="Боря")
    make_user(db, login="lockkid", group=g, first_name="Вася")
    for _ in range(5):
        login_guard.register_failure(db, "lockkid", "testclient")
    r = client.get(f"/teacher/groups/{g.id}")
    assert r.status_code == 200
    rows = {m.group(1): m.group(0) for m in re.finditer(r'<li[^>]*data-login="([^"]+)".*?</li>', r.text, re.S)}
    assert "не активен" not in rows["okkid"] and "заблокирован" not in rows["okkid"]
    assert "не активен" in rows["offkid"]
    assert "заблокирован" in rows["lockkid"]


# ---------- карточка ученика ----------


def test_student_page_actions(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    r = client.get(f"/teacher/students/{s.id}")
    assert r.status_code == 200
    assert f"/teacher/students/{s.id}/reset-password" in r.text and "data-confirm" in r.text
    assert "Деактивировать" in r.text and "Активировать" not in r.text.replace("Деактивировать", "")
    client.post(f"/teacher/students/{s.id}/deactivate")
    assert "Активировать" in client.get(f"/teacher/students/{s.id}").text


def test_reset_password_kicks_student(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    kid = kid_client()
    r = client.post(f"/teacher/students/{s.id}/reset-password")
    assert r.status_code == 200 and "больше он не покажется" in r.text
    assert r.headers["cache-control"] == "no-store"
    pwd = re.search(r'data-temp-password="([^"]+)"', r.text).group(1)
    db.refresh(s)
    assert s.must_change_password
    assert kid.get("/", follow_redirects=False).headers["location"] == "/login"
    assert login_as(client, "kid", pwd).headers["location"] == "/password/new"


def test_deactivate_logs_out_immediately(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    kid = kid_client()
    client.post(f"/teacher/students/{s.id}/deactivate")
    assert kid.get("/", follow_redirects=False).headers["location"] == "/login"
    assert login_as(kid, "kid").status_code == 200  # «Неверный логин или пароль»


def test_activate(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g, is_active=False)
    client.post(f"/teacher/students/{s.id}/activate")
    db.refresh(s)
    assert s.is_active
    assert login_as(client, "kid").headers["location"] == "/"


def test_unlock(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    for _ in range(5):
        login_guard.register_failure(db, "kid", "testclient")
    client.post(f"/teacher/students/{s.id}/unlock")
    assert login_guard.blocked_until(db, "kid", "testclient") is None


def test_unlock_clears_classroom_ip_block(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    login_guard.register_failure(db, "kid", "testclient")
    for i in range(19):
        login_guard.register_failure(db, f"other{i}", "testclient")  # 20 неудач с одного IP: блок на весь класс
    assert login_guard.blocked_until(db, "mate", "testclient") is not None
    assert "заблокирован" in client.get(f"/teacher/students/{s.id}").text
    assert "Разблокировать вход" in client.get(f"/teacher/students/{s.id}").text
    client.post(f"/teacher/students/{s.id}/unlock")
    db.expire_all()
    assert login_guard.blocked_until(db, "mate", "testclient") is None
    assert "Разблокировать вход" not in client.get(f"/teacher/students/{s.id}").text


def test_edit_student_name(client, db):
    t, g = setup_teacher(client, db)
    s = make_user(db, login="kid", group=g)
    r = client.post(
        f"/teacher/students/{s.id}/edit", data={"first_name": " Маша ", "last_name": "Ким", "group_id": g.id}
    )
    assert r.status_code == 200
    db.refresh(s)
    assert (s.first_name, s.last_name) == ("Маша", "Ким")
    r = client.post(f"/teacher/students/{s.id}/edit", data={"first_name": "", "last_name": "Ким", "group_id": g.id})
    assert "Введите имя и фамилию" in r.text
    db.refresh(s)
    assert s.first_name == "Маша"


def test_move_only_to_own_group(client, db):
    t, g = setup_teacher(client, db)
    g_own2 = make_group(db, t, name="Группа 2")
    other = make_user(db, role="teacher", login="teach2")
    g_foreign = make_group(db, other)
    s = make_user(db, login="kid", group=g)
    client.post(
        f"/teacher/students/{s.id}/edit", data={"first_name": "Петя", "last_name": "К", "group_id": g_foreign.id}
    )
    db.refresh(s)
    assert s.group_id == g.id
    client.post(f"/teacher/students/{s.id}/edit", data={"first_name": "Петя", "last_name": "К", "group_id": g_own2.id})
    db.refresh(s)
    assert s.group_id == g_own2.id
    r = client.post(f"/teacher/students/{s.id}/edit", data={"first_name": "Петя", "last_name": "К", "group_id": "abc"})
    assert r.status_code == 404


def test_create_student_rejects_bad_name(client, db):
    seed_filter(db)
    t, g = setup_teacher(client, db)
    r = new_student(client, g, first_name="Сука", login="ok.login")
    assert "В имени или фамилии недопустимое слово" in r.text
    assert db.query(User).filter_by(role="student").count() == 0
    assert new_student(client, g, first_name="Мандарин", login="ok.login2").status_code == 200
    assert db.query(User).filter_by(login="ok.login2").count() == 1
