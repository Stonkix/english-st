"""sqladmin не должен давать сохранить значения, которые молча ломают аккаунты."""

import re

from app.models import Group, User
from tests.factories import login_as, make_group, make_user


def admin(client, db):
    boss = make_user(db, role="admin", login="boss")
    login_as(client, "boss")
    return boss


def user_form(u, **over):
    data = {
        "login": u.login,
        "role": u.role,
        "first_name": u.first_name,
        "last_name": u.last_name,
        "group": str(u.group_id) if u.group_id else "",
        "is_active": "y",
    }
    data.update(over)
    return {k: v for k, v in data.items() if v is not None}


def field_html(page: str, name: str) -> str:
    """Тег <input>/<select> поля формы целиком (select — вместе с вариантами)."""
    m = re.search(rf'<select[^>]*name="{name}".*?</select>|<input[^>]*name="{name}"[^>]*>', page, re.S)
    assert m, f"нет поля {name}"
    return m.group(0)


def test_user_delete_is_disabled(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    assert "/admin/user/delete" not in client.get("/admin/user/list").text
    client.delete(f"/admin/user/delete?pks={kid.id}")
    db.expire_all()
    assert db.get(User, kid.id) is not None


def test_group_delete_is_disabled(client, db):
    admin(client, db)
    group = make_group(db, make_user(db, role="teacher", login="teach1"))
    assert "/admin/group/delete" not in client.get("/admin/group/list").text
    client.delete(f"/admin/group/delete?pks={group.id}")
    db.expire_all()
    assert db.get(Group, group.id) is not None


def test_role_is_a_select_of_known_roles(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    html = field_html(client.get(f"/admin/user/edit/{kid.id}").text, "role")
    assert html.startswith("<select")
    for role in User.ROLES:
        assert f'value="{role}"' in html


def test_unknown_role_is_rejected(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    client.post(f"/admin/user/edit/{kid.id}", data=user_form(kid, role="Admin"))
    db.expire_all()
    assert db.get(User, kid.id).role == "student"


def test_login_is_normalized_on_save(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    r = client.post(f"/admin/user/edit/{kid.id}", data=user_form(kid, login="  Kid.Two "), follow_redirects=False)
    assert r.status_code in (302, 303)
    db.expire_all()
    assert db.get(User, kid.id).login == "kid.two"


def test_invalid_login_is_rejected_with_message(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    r = client.post(f"/admin/user/edit/{kid.id}", data=user_form(kid, login="Кид!"))
    assert r.status_code == 400 and "Логин" in r.text
    db.expire_all()
    assert db.get(User, kid.id).login == "kid1"


def test_taken_login_is_rejected_with_message(client, db):
    admin(client, db)
    make_user(db, login="taken")
    kid = make_user(db, login="kid1")
    r = client.post(f"/admin/user/edit/{kid.id}", data=user_form(kid, login="TAKEN"))
    assert r.status_code == 400 and "занят" in r.text
    db.expire_all()
    assert db.get(User, kid.id).login == "kid1"


def test_edit_with_same_login_still_works(client, db):
    admin(client, db)
    kid = make_user(db, login="kid1")
    r = client.post(f"/admin/user/edit/{kid.id}", data=user_form(kid, first_name="Маша"), follow_redirects=False)
    assert r.status_code in (302, 303)
    db.expire_all()
    assert db.get(User, kid.id).first_name == "Маша"


def test_group_teacher_choices_are_only_teachers_and_admins(client, db):
    admin(client, db)
    make_user(db, role="teacher", login="teach1")
    make_user(db, login="pupil1")
    group = make_group(db, make_user(db, role="teacher", login="teach2"))
    html = field_html(client.get(f"/admin/group/edit/{group.id}").text, "teacher")
    assert "teach1" in html and "teach2" in html and "boss" in html
    assert "pupil1" not in html


def test_group_cannot_get_a_student_as_teacher(client, db):
    admin(client, db)
    pupil = make_user(db, login="pupil1")
    group = make_group(db, make_user(db, role="teacher", login="teach1"))
    teacher_id = group.teacher_id
    client.post(f"/admin/group/edit/{group.id}", data={"name": "Новая", "teacher": str(pupil.id)})
    db.expire_all()
    assert db.get(Group, group.id).teacher_id == teacher_id


def test_group_form_has_no_students_field(client, db):
    admin(client, db)
    group = make_group(db, make_user(db, role="teacher", login="teach1"))
    page = client.get(f"/admin/group/edit/{group.id}").text
    assert 'name="students"' not in page and 'name="teacher"' in page


def test_journals_are_read_only(client, db):
    from tests.factories import login_as, make_user
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    for slug in ("stamp-transaction", "order", "chat-message"):
        assert client.get(f"/admin/{slug}/list").status_code == 200, slug
        assert client.get(f"/admin/{slug}/create").status_code in (403, 404), slug
