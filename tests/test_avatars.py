import os
import re
from datetime import datetime, timedelta
from io import BytesIO

import pytest
from PIL import Image
from sqlalchemy import select

from app.avatars import avatar_locked, history, lock_message, set_avatar_lock
from app.config import settings
from app.images import save_avatar
from app.models import AvatarHistory, User
from app.timeutil import utcnow
from tests.factories import login_as, make_group, make_user

BAD_IMAGE = "Не получилось открыть картинку"


def jpeg(color="red", w=600, h=600):
    b = BytesIO()
    Image.new("RGB", (w, h), color).save(b, "JPEG")
    return b.getvalue()


def upload(client, color="red"):
    return client.post("/profile/avatar", files={"photo": ("a.jpg", jpeg(color), "image/jpeg")})


def setup(db):
    """Учитель с группой и ученик в ней."""
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t)
    s = make_user(db, login="kid", group=g)
    return t, s


def as_user(client, login):
    client.post("/logout")
    assert login_as(client, login).status_code == 303


def three_photos(client, db, s):
    """Ученик трижды меняет фото; возвращает пути A, B, C."""
    as_user(client, s.login)
    paths = []
    for color in ("red", "green", "blue"):
        upload(client, color)
        db.refresh(s)
        paths.append(s.avatar)
    return paths


def test_upload_limit_is_10mb(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    r = client.post("/profile/avatar", files={"photo": ("a.jpg", b"0" * (10 * 1024 * 1024 + 1), "image/jpeg")})
    assert BAD_IMAGE in r.text


def test_compressed_size_cap():
    noise = Image.frombytes("RGB", (4000, 3000), os.urandom(4000 * 3000 * 3))
    b = BytesIO()
    noise.save(b, "JPEG", quality=60)
    assert len(b.getvalue()) < 10 * 1024 * 1024
    rel = save_avatar(1, b.getvalue())
    assert (settings.media_dir / rel).stat().st_size <= 400 * 1024
    assert Image.open(settings.media_dir / rel).size == (512, 512)


def test_quality_steps_down_until_under_cap(monkeypatch):
    import app.images as images

    noise = Image.frombytes("RGB", (800, 800), os.urandom(800 * 800 * 3))
    b = BytesIO()
    noise.save(b, "PNG")
    monkeypatch.setattr(images, "AVATAR_MAX_BYTES", 100 * 1024)  # шум при качестве 85 заведомо больше
    rel = save_avatar(1, b.getvalue())
    assert (settings.media_dir / rel).stat().st_size <= 100 * 1024


def test_student_changes_keep_all_previous(client, db):
    t, s = setup(db)
    a, b, c = three_photos(client, db, s)
    assert [i.path for i in history(db, s)] == [b, a]  # новые сверху; текущее (c) в истории не числится
    assert s.avatar == c
    for rel in (a, b, c):
        assert (settings.media_dir / rel).exists()


def test_student_delete_goes_to_history(client, db):
    t, s = setup(db)
    as_user(client, "kid")
    upload(client)
    db.refresh(s)
    cur = s.avatar
    client.post("/profile/avatar/delete")
    db.refresh(s)
    assert s.avatar is None and [i.path for i in history(db, s)] == [cur]
    assert (settings.media_dir / cur).exists()


def test_previous_visible_only_to_teacher_and_admin(client, db):
    t, s = setup(db)
    make_user(db, role="admin", login="adm")
    make_user(db, role="teacher", login="teach2")
    make_user(db, login="mate", group=s.group)
    a, b, _ = three_photos(client, db, s)
    for who, ok in [("teach", True), ("adm", True), ("kid", False), ("mate", False), ("teach2", False)]:
        as_user(client, who)
        assert (client.get(f"/media/{a}").status_code == 200) is ok, who
        assert (client.get(f"/media/{b}").status_code == 200) is ok, who


def test_teacher_delete_current_is_permanent(client, db):
    t, s = setup(db)
    a, b, c = three_photos(client, db, s)
    as_user(client, "teach")
    r = client.post(f"/teacher/students/{s.id}/avatar/delete", follow_redirects=False)
    assert r.status_code == 303
    db.refresh(s)
    assert s.avatar is None and not (settings.media_dir / c).exists()
    assert [i.path for i in history(db, s)] == [b, a]


def test_teacher_uploads_for_student_and_deletes_history_item(client, db):
    t, s = setup(db)
    as_user(client, "kid")
    upload(client, "red")
    db.refresh(s)
    first = s.avatar
    as_user(client, "teach")
    files = {"photo": ("a.jpg", jpeg("blue"), "image/jpeg")}
    assert client.post(f"/teacher/students/{s.id}/avatar", files=files, follow_redirects=False).status_code == 303
    db.refresh(s)
    assert s.avatar != first and (settings.media_dir / s.avatar).exists()
    items = history(db, s)
    assert [i.path for i in items] == [first]  # прежнее текущее ушло в историю, как при замене учеником
    assert "Предыдущие фото" in client.get(f"/teacher/students/{s.id}").text
    client.post(f"/teacher/students/{s.id}/avatar/history/{items[0].id}/delete")
    assert history(db, s) == [] and not (settings.media_dir / first).exists()


def test_teacher_upload_bad_image(client, db):
    t, s = setup(db)
    as_user(client, "teach")
    r = client.post(f"/teacher/students/{s.id}/avatar", files={"photo": ("a.jpg", b"junk", "image/jpeg")})
    assert r.status_code == 200 and BAD_IMAGE in r.text


def test_delete_one_history_item_keeps_the_other(client, db):
    t, s = setup(db)
    a, b, c = three_photos(client, db, s)
    items = history(db, s)  # [b, a]
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/history/{items[0].id}/delete")
    assert not (settings.media_dir / b).exists() and (settings.media_dir / a).exists()
    assert [i.path for i in history(db, s)] == [a]


def test_foreign_teacher_cannot_delete_history_item(client, db):
    t, s = setup(db)
    other = make_user(db, role="teacher", login="teach2")
    other_student = make_user(db, login="kid2", group=make_group(db, other, "Другая"))
    three_photos(client, db, s)
    item = history(db, s)[0]
    as_user(client, "teach2")
    assert client.post(f"/teacher/students/{s.id}/avatar/history/{item.id}/delete").status_code == 404
    # свой ученик, но чужой item_id
    assert client.post(f"/teacher/students/{other_student.id}/avatar/history/{item.id}/delete").status_code == 404
    assert (settings.media_dir / item.path).exists() and len(history(db, s)) == 2


def test_deleting_user_cascades_history(client, db):
    t, s = setup(db)
    three_photos(client, db, s)
    uid = s.id
    assert db.scalars(select(AvatarHistory).where(AvatarHistory.user_id == uid)).all()
    db.delete(db.get(User, uid))
    db.commit()
    assert db.scalars(select(AvatarHistory).where(AvatarHistory.user_id == uid)).all() == []


def test_lock_blocks_student(client, db):
    t, s = setup(db)
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": "7"})
    as_user(client, "kid")
    assert upload(client).status_code == 403
    assert client.post("/profile/avatar/delete").status_code == 403
    for r in (upload(client), client.post("/profile/avatar/delete")):  # устаревшая страница: объясняем, а не «Сюда нельзя»
        assert "Учитель запретил менять фото до" in r.text and "Сюда нельзя" not in r.text
    page = client.get("/profile").text
    assert "Учитель запретил менять фото до" in page and 'action="/profile/avatar"' not in page
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": "off"})
    as_user(client, "kid")
    assert upload(client).status_code == 200  # после редиректа — страница профиля
    db.refresh(s)
    assert s.avatar


def selected_lock_option(page: str) -> str:
    select = re.search(r'<select id="lock-period".*?</select>', page, re.S).group(0)
    return re.search(r'<option value="([^"]*)"[^>]* selected', select).group(1)


def test_lock_dropdown_keeps_timed_lock_by_default(client, db):
    t, s = setup(db)
    as_user(client, "teach")
    url = f"/teacher/students/{s.id}"
    assert selected_lock_option(client.get(url).text) == "off"  # запрета нет
    for period in ("7", "forever"):
        client.post(f"{url}/avatar/lock", data={"period": period})
        page = client.get(url).text
        assert selected_lock_option(page) == ""
        assert "Оставить как есть" in page.split('id="lock-period"')[1].split("<option", 2)[1]  # первый вариант


def test_lock_empty_period_changes_nothing(client, db):
    t, s = setup(db)
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": "7"})
    db.refresh(s)
    until = s.avatar_locked_until
    r = client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": ""}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(s)
    assert s.avatar_locked_until == until and not s.avatar_locked_forever


def test_lock_forever_message_and_teacher_upload_ignores_lock(client, db):
    t, s = setup(db)
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": "forever"})
    files = {"photo": ("a.jpg", jpeg(), "image/jpeg")}
    assert client.post(f"/teacher/students/{s.id}/avatar", files=files, follow_redirects=False).status_code == 303
    as_user(client, "kid")
    assert "Учитель запретил менять фото навсегда" in client.get("/profile").text


def test_lock_only_restricts_students(client, db):
    t = make_user(db, role="teacher", login="teach")
    set_avatar_lock(db, t, "forever")
    login_as(client, "teach")
    assert upload(client).status_code == 200
    db.refresh(t)
    assert t.avatar


def test_lock_forever_and_expiry(db):
    u = make_user(db)
    now = utcnow()
    set_avatar_lock(db, u, "1", now=now)
    assert avatar_locked(u, now + timedelta(hours=23)) and not avatar_locked(u, now + timedelta(hours=25))
    set_avatar_lock(db, u, "forever", now=now)
    assert avatar_locked(u, now + timedelta(days=3650))
    set_avatar_lock(db, u, "off")
    assert not avatar_locked(u, now)
    with pytest.raises(ValueError):
        set_avatar_lock(db, u, "5")


def test_lock_message_uses_moscow_time(db):
    u = make_user(db)
    u.avatar_locked_until = datetime(2030, 1, 1, 21, 30)  # 21:30 UTC = 00:30 по Москве следующего дня
    assert lock_message(u, now=datetime(2029, 12, 1)) == "Учитель запретил менять фото до 02.01.2030 00:30"


def test_foreign_teacher_cannot_moderate(client, db):
    t, s = setup(db)
    make_user(db, role="teacher", login="teach2")
    as_user(client, "teach2")
    assert client.post(f"/teacher/students/{s.id}/avatar/lock", data={"period": "7"}).status_code == 404
    assert client.post(f"/teacher/students/{s.id}/avatar/delete").status_code == 404
    files = {"photo": ("a.jpg", jpeg(), "image/jpeg")}
    assert client.post(f"/teacher/students/{s.id}/avatar", files=files).status_code == 404
    db.refresh(s)
    assert not avatar_locked(s) and s.avatar is None


def test_student_page_has_confirm_and_once(client, db):
    t, s = setup(db)
    three_photos(client, db, s)
    as_user(client, "teach")
    html = client.get(f"/teacher/students/{s.id}").text
    assert html.count("/avatar/history/") == 2 and html.count("data-confirm") >= 4
    reset = html[html.index(f"/teacher/students/{s.id}/reset-password"):]
    assert "data-once" in reset.split(">", 1)[0]


def test_media_history_item_removed_is_404(client, db):
    t, s = setup(db)
    a, _, _ = three_photos(client, db, s)
    item = history(db, s)[-1]
    as_user(client, "teach")
    client.post(f"/teacher/students/{s.id}/avatar/history/{item.id}/delete")
    assert client.get(f"/media/{a}").status_code == 404
