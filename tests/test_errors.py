"""Пользователь никогда не видит сырой JSON 422 или текст 500: только страницы сайта."""

import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.factories import login_as, make_group, make_user

BIG = "99999999999999999999"
UTF7_LOGIN = (
    b'--b\r\nContent-Disposition: form-data; name="login"\r\n\r\n+2D8-\r\n'
    b'--b\r\nContent-Disposition: form-data; name="password"\r\n\r\nx\r\n--b--\r\n'
)


@pytest.fixture
def safe_client():
    """Как настоящий сервер: необработанное исключение не пробрасывается в тест, а превращается в ответ."""
    with TestClient(app, headers={"Origin": "http://testserver"}, raise_server_exceptions=False) as c:
        yield c


def assert_html(r, status):
    assert r.status_code == status, r.text[:200]
    assert r.headers["content-type"].startswith("text/html")
    assert "English ST" in r.text  # оформленная страница сайта, а не JSON или голый текст


@pytest.fixture
def world(db):
    teacher = make_user(db, role="teacher", login="teach1")
    group = make_group(db, teacher)
    student = make_user(db, login="pupil1", group=group)
    make_user(db, role="admin", login="admin1")
    return teacher, group, student


def test_non_numeric_path_id_is_404_page(safe_client, world):
    login_as(safe_client, "teach1")
    assert_html(safe_client.get("/teacher/groups/abc"), 404)


def test_non_numeric_teacher_id_is_404_page(safe_client, world):
    login_as(safe_client, "admin1")
    assert_html(safe_client.post("/school/teachers/abc/deactivate"), 404)


@pytest.mark.parametrize(
    "method, path",
    [
        ("get", f"/teacher/groups/{BIG}"),
        ("get", f"/teacher/students/{BIG}"),
        ("post", f"/teacher/students/{BIG}/avatar/delete"),
        ("get", "/teacher/groups/0"),
        ("get", "/teacher/groups/-5"),
    ],
)
def test_huge_or_nonpositive_ids_are_404_page(safe_client, world, method, path):
    login_as(safe_client, "teach1")
    assert_html(getattr(safe_client, method)(path), 404)


def test_huge_history_item_id_is_404_page(safe_client, world):
    _, _, student = world
    login_as(safe_client, "teach1")
    assert_html(safe_client.post(f"/teacher/students/{student.id}/avatar/history/{BIG}/delete"), 404)


def test_huge_teacher_id_is_404_page(safe_client, world):
    login_as(safe_client, "admin1")
    assert_html(safe_client.post(f"/school/teachers/{BIG}/deactivate"), 404)


@pytest.mark.parametrize("group_id", [BIG, "abc", "0", "-1", ""])
def test_edit_with_bad_group_id_is_404_page(safe_client, world, group_id):
    _, _, student = world
    login_as(safe_client, "teach1")
    r = safe_client.post(
        f"/teacher/students/{student.id}/edit", data={"first_name": "a", "last_name": "b", "group_id": group_id}
    )
    assert_html(r, 404)


def test_avatar_text_field_instead_of_file_is_400_page(safe_client, world):
    login_as(safe_client, "pupil1")
    assert_html(safe_client.post("/profile/avatar", data={"photo": "text"}), 400)


def test_teacher_avatar_text_field_is_400_page(safe_client, world):
    _, _, student = world
    login_as(safe_client, "teach1")
    assert_html(safe_client.post(f"/teacher/students/{student.id}/avatar", data={"photo": "text"}), 400)


def test_validation_error_page_has_no_json(safe_client, world):
    login_as(safe_client, "pupil1")
    r = safe_client.post("/profile/avatar", data={"photo": "text"})
    assert r.text.lstrip().startswith("<!doctype html>") and "\"detail\"" not in r.text


def test_lone_surrogate_in_multipart_login_is_not_500(safe_client, world):
    r = safe_client.post(
        "/login", content=UTF7_LOGIN, headers={"Content-Type": "multipart/form-data; boundary=b; charset=utf-7"}
    )
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_lone_surrogate_in_multipart_password_is_not_500(safe_client, world):
    body = UTF7_LOGIN.replace(b'name="login"', b'name="x"').replace(
        b'name="password"\r\n\r\nx', b'name="password"\r\n\r\n+2D8-'
    )
    r = safe_client.post("/login", content=body, headers={"Content-Type": "multipart/form-data; boundary=b; charset=utf-7"})
    assert r.status_code == 200 and "Неверный логин или пароль" in r.text


def test_unexpected_exception_renders_500_page(safe_client, world, monkeypatch):
    import app.routes.teacher as mod

    def boom(*a, **k):
        raise RuntimeError("внезапный сбой")

    monkeypatch.setattr(mod, "_groups_page", boom)
    login_as(safe_client, "teach1")
    r = safe_client.get("/teacher")
    assert_html(r, 500)
    assert "RuntimeError" not in r.text and "внезапный" not in r.text


def test_500_page_does_not_touch_db(safe_client, world, monkeypatch):
    """Если упала сама БД, страница ошибки не должна падать вслед за ней."""
    import app.main as main_mod

    def broken(*a, **k):
        raise AssertionError("обращение к БД на странице 500")

    monkeypatch.setattr(main_mod, "SessionLocal", broken)
    import app.routes.teacher as mod

    monkeypatch.setattr(mod, "_groups_page", lambda *a, **k: 1 / 0)
    login_as(safe_client, "teach1")
    assert_html(safe_client.get("/teacher"), 500)
