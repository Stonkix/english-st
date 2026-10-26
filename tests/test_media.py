from io import BytesIO

import pytest
from PIL import Image

from app.auth.access import can_view_avatar
from app.config import settings
from tests.factories import login_as, make_group, make_user


def jpeg(w=600, h=600):
    b = BytesIO()
    Image.new("RGB", (w, h), "red").save(b, "JPEG")
    return b.getvalue()


def give_avatar(client, db, user):
    """Заходит под user, грузит фото, выходит. Возвращает URL файла."""
    login_as(client, user.login)
    client.post("/profile/avatar", files={"photo": ("a.jpg", jpeg(), "image/jpeg")})
    client.post("/logout")
    db.refresh(user)
    return f"/media/{user.avatar}"


def test_avatar_visibility(client, db):
    t = make_user(db, role="teacher", login="teach1")
    t2 = make_user(db, role="teacher", login="teach2")
    g, g2 = make_group(db, t), make_group(db, t2)
    a = make_user(db, login="pupil-a", group=g)
    make_user(db, login="pupil-b", group=g)
    make_user(db, login="pupil-x", group=g2)
    make_user(db, role="admin", login="adm")
    make_user(db, login="loner")
    url = give_avatar(client, db, a)

    anon = client.get(url, follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"] == "/login"
    for who, ok in [
        ("pupil-a", True), ("pupil-b", True), ("teach1", True), ("adm", True),
        ("pupil-x", False), ("teach2", False), ("loner", False),
    ]:
        login_as(client, who)
        assert (client.get(url).status_code == 200) is ok, who
        client.post("/logout")


def test_student_sees_teacher_avatar_and_not_other_teacher(client, db):
    t = make_user(db, role="teacher", login="teach1")
    t2 = make_user(db, role="teacher", login="teach2")
    g = make_group(db, t)
    make_user(db, login="kid", group=g)
    url = give_avatar(client, db, t)
    url2 = give_avatar(client, db, t2)
    login_as(client, "kid")
    assert client.get(url).status_code == 200
    assert client.get(url2).status_code == 404


def test_debug_serves_file_with_private_cache(client, db):
    u = make_user(db, login="kid")
    url = give_avatar(client, db, u)
    login_as(client, "kid")
    r = client.get(url)
    assert r.status_code == 200 and r.headers["content-type"] == "image/webp"
    assert Image.open(BytesIO(r.content)).format == "WEBP"
    assert r.headers["cache-control"] == "private, max-age=86400"


def test_prod_uses_x_accel(client, db, monkeypatch):
    u = make_user(db, login="kid")
    url = give_avatar(client, db, u)
    login_as(client, "kid")
    monkeypatch.setattr(settings, "debug", False)
    r = client.get(url)
    assert r.status_code == 200 and r.content == b""
    assert r.headers["x-accel-redirect"] == f"/_protected/{u.avatar}"
    assert r.headers["cache-control"] == "private, max-age=86400"


def test_old_avatar_is_404(client, db):
    u = make_user(db, login="kid")
    old_url = give_avatar(client, db, u)
    new_url = give_avatar(client, db, u)
    assert old_url != new_url
    login_as(client, "kid")
    assert client.get(old_url).status_code == 404
    assert client.get(new_url).status_code == 200


def test_deleted_avatar_is_404(client, db):
    u = make_user(db, login="kid")
    url = give_avatar(client, db, u)
    login_as(client, "kid")
    client.post("/profile/avatar/delete")
    assert client.get(url).status_code == 404


def test_missing_file_is_404(client, db):
    u = make_user(db, login="kid")
    url = give_avatar(client, db, u)
    (settings.media_dir / u.avatar).unlink()
    login_as(client, "kid")
    assert client.get(url).status_code == 404


@pytest.mark.parametrize(
    "path",
    [
        "avatars/{id}/abc.webp",  # не 12 hex
        "avatars/{id}/{name}.png",
        "avatars/{id}/{name}.webp.bak",
        "avatars/{id}/{NAME}.webp",  # верхний регистр
        "avatars/{id}/%2e%2e%2f{name}.webp",
        "avatars/{id}/..%2f..%2fapp%2fmain.py",
        "avatars/abc/{name}.webp",
        "avatars/-1/{name}.webp",
        "avatars/%C2%B2/{name}.webp",  # «²» — цифра для str.isdigit, но не число
        "avatars/{id}%0A/{name}.webp",
        "avatars/99999999999999999999999/{name}.webp",
        "avatars/9999999/{name}.webp",  # нет такого владельца
    ],
)
def test_media_rejects_bad_paths(client, db, path):
    u = make_user(db, login="kid")
    url = give_avatar(client, db, u)
    name = u.avatar.rsplit("/", 1)[1].removesuffix(".webp")
    login_as(client, "kid")
    r = client.get("/media/" + path.format(id=u.id, name=name, NAME=name.upper()))
    assert r.status_code == 404


def test_media_not_mounted_statically(client, db):
    from starlette.routing import Mount

    from app.main import app

    assert not [r for r in app.routes if isinstance(r, Mount) and not r.path.startswith(("/static", "/admin"))]  # /admin — sqladmin
    u = make_user(db, login="kid")
    give_avatar(client, db, u)
    assert client.get(f"/static/{u.avatar}").status_code == 404
    assert client.get(f"/media/{u.avatar}", follow_redirects=False).status_code == 303  # аноним


# ---------- can_view_avatar ----------

def test_can_view_avatar_rules(db):
    t = make_user(db, role="teacher", login="teach1")
    t2 = make_user(db, role="teacher", login="teach2")
    admin = make_user(db, role="admin", login="adm")
    g, g2 = make_group(db, t), make_group(db, t2)
    a = make_user(db, login="pupil-a", group=g)
    b = make_user(db, login="pupil-b", group=g)
    x = make_user(db, login="pupil-x", group=g2)
    loner = make_user(db, login="loner")

    assert can_view_avatar(a, a) and can_view_avatar(loner, loner)
    assert can_view_avatar(admin, a) and can_view_avatar(admin, t)
    assert can_view_avatar(t, a) and not can_view_avatar(t, x) and not can_view_avatar(t, t2)
    assert can_view_avatar(a, b) and can_view_avatar(a, t)
    assert not can_view_avatar(a, x) and not can_view_avatar(a, t2) and not can_view_avatar(a, admin)
    assert not can_view_avatar(loner, a) and not can_view_avatar(a, loner)
    assert not can_view_avatar(loner, make_user(db, login="loner2"))  # оба без группы — не одногруппники


# ---------- картинки вопросов ----------

def test_can_view_question_image_rules(db):
    from app.auth.access import can_view_question_image

    t = make_user(db, role="teacher", login="teach1")
    t2 = make_user(db, role="teacher", login="teach2")
    admin = make_user(db, role="admin", login="adm")
    g, g2 = make_group(db, t), make_group(db, t2)
    a = make_user(db, login="pupil-a", group=g)
    x = make_user(db, login="pupil-x", group=g2)
    loner = make_user(db, login="loner")

    assert can_view_question_image(t, t.id, db) and can_view_question_image(admin, t.id, db)
    assert can_view_question_image(a, t.id, db)
    assert not can_view_question_image(x, t.id, db) and not can_view_question_image(t2, t.id, db)
    assert not can_view_question_image(loner, t.id, db)


@pytest.mark.parametrize(
    "path",
    [
        "questions/{id}/abc.webp",
        "questions/{id}/{name}.png",
        "questions/{id}/{NAME}.webp",
        "questions/{id}/..%2f..%2fapp%2fmain.py",
        "questions/abc/{name}.webp",
        "questions/007/{name}.webp",
        "questions/99999999999999999999999/{name}.webp",
    ],
)
def test_question_media_rejects_bad_paths(client, db, path):
    t = make_user(db, role="teacher", login="teach1")
    login_as(client, "teach1")
    r = client.get("/media/" + path.format(id=t.id, name="0123456789ab", NAME="0123456789AB"))
    assert r.status_code == 404
