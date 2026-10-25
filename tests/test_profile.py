from io import BytesIO

import pytest
from PIL import Image

from app.config import settings
from app.images import MAX_UPLOAD_BYTES, InvalidImage, read_limited
from tests.factories import login_as, make_user

BAD_IMAGE = "Не получилось открыть картинку. Попробуйте другое фото."


def jpeg(w=3000, h=2000, **save_kwargs):
    b = BytesIO()
    Image.new("RGB", (w, h), "red").save(b, "JPEG", **save_kwargs)
    return b.getvalue()


def upload(client, data, name="a.jpg", mime="image/jpeg"):
    return client.post("/profile/avatar", files={"photo": (name, data, mime)})


def test_profile_requires_login(client):
    r = client.get("/profile", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/login"


def test_profile_shows_name_read_only(client, db):
    make_user(db, login="kid", first_name="Маша", last_name="Лисицына")
    login_as(client, "kid")
    r = client.get("/profile")
    assert r.status_code == 200
    assert "Маша Лисицына" in r.text and "kid" in r.text
    assert 'name="first_name"' not in r.text and 'name="last_name"' not in r.text
    assert ">МЛ<" in r.text.replace(" ", "").replace("\n", "")  # без фото — кружок с инициалами
    assert "Выйти" in r.text


def test_avatar_upload_square_webp(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    r = upload(client, jpeg())
    assert r.status_code in (200, 303)
    db.refresh(u)
    img = Image.open(settings.media_dir / u.avatar)
    assert img.format == "WEBP" and img.size == (512, 512) and not img.getexif()
    assert u.avatar.startswith(f"avatars/{u.id}/")
    assert f'src="/media/{u.avatar}"' in client.get("/profile").text


def test_avatar_strips_exif_gps(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    exif = Image.Exif()
    exif[0x0112] = 6  # поворот
    exif.get_ifd(0x8825)[2] = (55.0, 45.0, 0.0)  # GPS
    exif.get_ifd(0x8825)[1] = "N"
    upload(client, jpeg(400, 300, exif=exif))
    db.refresh(u)
    raw = (settings.media_dir / u.avatar).read_bytes()
    assert b"EXIF" not in raw and b"XMP" not in raw
    assert not Image.open(BytesIO(raw)).getexif()


def test_avatar_replaced_removes_old_file(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    upload(client, jpeg(600, 600))
    db.refresh(u)
    old = settings.media_dir / u.avatar
    assert old.exists()
    upload(client, jpeg(600, 600))
    db.refresh(u)
    assert (settings.media_dir / u.avatar).exists() and not old.exists()


def test_avatar_delete(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    upload(client, jpeg(600, 600))
    db.refresh(u)
    path = settings.media_dir / u.avatar
    r = client.post("/profile/avatar/delete")
    assert r.status_code == 200
    db.refresh(u)
    assert u.avatar is None and not path.exists()
    assert "/media/" not in r.text


def test_avatar_not_image(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    r = upload(client, b"not an image")
    assert r.status_code == 200 and BAD_IMAGE in r.text


def test_avatar_empty_file_and_no_file(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    assert BAD_IMAGE in upload(client, b"").text
    r = client.post("/profile/avatar", data={"x": "1"})
    assert r.status_code == 200 and BAD_IMAGE in r.text


def test_avatar_truncated_image(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    r = upload(client, jpeg(2000, 2000)[:500])
    assert r.status_code == 200 and BAD_IMAGE in r.text


def test_avatar_too_big(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    r = upload(client, b"0" * (15 * 1024 * 1024 + 1))
    assert r.status_code == 200 and BAD_IMAGE in r.text


def test_avatar_decompression_bomb(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    b = BytesIO()
    Image.new("1", (30000, 30000)).save(b, "PNG")  # мало байт, огромная картинка
    assert len(b.getvalue()) < MAX_UPLOAD_BYTES
    r = upload(client, b.getvalue(), "a.png", "image/png")
    assert r.status_code == 200 and BAD_IMAGE in r.text
    db.refresh(u)
    assert u.avatar is None


def test_avatar_failed_upload_keeps_old_avatar(client, db):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    upload(client, jpeg(600, 600))
    db.refresh(u)
    old = u.avatar
    upload(client, b"garbage")
    db.refresh(u)
    assert u.avatar == old and (settings.media_dir / old).exists()


def test_avatar_heic(client, db):
    import pillow_heif

    pillow_heif.register_heif_opener()
    buf = BytesIO()
    try:
        Image.new("RGB", (300, 200), "blue").save(buf, "HEIF")
    except Exception as exc:  # pragma: no cover — зависит от сборки pillow_heif
        pytest.skip(f"pillow_heif не умеет сохранять HEIF в этом окружении: {exc}")
    u = make_user(db, login="kid")
    login_as(client, "kid")
    r = upload(client, buf.getvalue(), "IMG_0001.HEIC", "image/heic")
    assert r.status_code in (200, 303)
    db.refresh(u)
    assert u.avatar and Image.open(settings.media_dir / u.avatar).size == (512, 512)


def test_read_limited_stops_after_limit():
    class Endless:
        read_total = 0

        def read(self, n):
            self.read_total += n
            return b"0" * n

    stream = Endless()
    with pytest.raises(InvalidImage):
        read_limited(stream)
    assert stream.read_total == MAX_UPLOAD_BYTES + 1


def test_read_limited_small():
    assert read_limited(BytesIO(b"abc")) == b"abc"
    assert len(read_limited(BytesIO(b"0" * MAX_UPLOAD_BYTES))) == MAX_UPLOAD_BYTES


def test_avatar_url_filter(db):
    from types import SimpleNamespace

    from app.templating import templates

    f = templates.env.filters["avatar_url"]
    assert f(SimpleNamespace(avatar="avatars/1/abc.webp")) == "/media/avatars/1/abc.webp"
    assert f(SimpleNamespace(avatar=None)) is None


# ---------- смена пароля ----------

def change(client, current="Passw0rd12", new="NewPassw0rd", repeat=None):
    return client.post(
        "/profile/password",
        data={"current": current, "new": new, "repeat": new if repeat is None else repeat},
    )


def test_change_password_revokes_other_sessions(client, db):
    from fastapi.testclient import TestClient

    from app.main import app

    make_user(db, login="kid")
    other = TestClient(app, headers={"Origin": "http://testserver"})
    login_as(other, "kid")
    login_as(client, "kid")
    r = change(client)
    assert "Пароль изменён" in r.text
    assert other.get("/profile", follow_redirects=False).headers["location"] == "/login"
    assert client.get("/profile").status_code == 200


def test_change_password_new_works_old_does_not(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    change(client)
    client.post("/logout")
    assert login_as(client, "kid", "Passw0rd12").status_code == 200  # осталась форма входа
    assert login_as(client, "kid", "NewPassw0rd").status_code == 303


@pytest.mark.parametrize(
    "kwargs, message",
    [
        ({"current": "Wrong0Password"}, "Текущий пароль указан неверно"),
        ({"new": "short1A", "repeat": "short1A"}, "Не короче 10 символов"),
        ({"new": "Привет12345"}, "Только латиница"),
        ({"repeat": "Other0Password"}, "Пароли не совпадают"),
        ({"new": "Passw0rd12"}, "Новый пароль совпадает с текущим"),
        ({"new": "Aa1" + "x" * 254}, "Не длиннее 256 символов"),
    ],
)
def test_change_password_errors(client, db, kwargs, message):
    u = make_user(db, login="kid")
    login_as(client, "kid")
    old_hash = u.password_hash
    r = change(client, **kwargs)
    assert r.status_code == 200 and message in r.text
    assert "Пароль изменён" not in r.text
    db.refresh(u)
    assert u.password_hash == old_hash


def test_change_password_too_long_current_is_wrong_not_500(client, db):
    make_user(db, login="kid")
    login_as(client, "kid")
    r = change(client, current="A1a" * 200)
    assert r.status_code == 200 and "Текущий пароль указан неверно" in r.text


def test_change_password_keeps_other_users_sessions(client, db):
    from fastapi.testclient import TestClient

    from app.main import app

    make_user(db, login="kid")
    make_user(db, login="friend")
    friend = TestClient(app, headers={"Origin": "http://testserver"})
    login_as(friend, "friend")
    login_as(client, "kid")
    change(client)
    assert friend.get("/profile").status_code == 200


# ---------- память и параллельность при декодировании ----------

def test_decode_max_allowed_size_small_png_ok(client, db):
    from app.images import MAX_PIXELS

    u = make_user(db, login="kid")
    login_as(client, "kid")
    side = int(MAX_PIXELS**0.5)
    b = BytesIO()
    Image.new("1", (side, side)).save(b, "PNG")  # несколько КБ, ~50 Мп
    assert len(b.getvalue()) < 100_000
    r = upload(client, b.getvalue(), "a.png", "image/png")
    assert r.status_code in (200, 303)
    db.refresh(u)
    assert Image.open(settings.media_dir / u.avatar).size == (512, 512)


def test_decode_over_pixel_cap_is_invalid():
    from app.images import MAX_PIXELS, _decode_square

    side = int(MAX_PIXELS**0.5) + 100
    b = BytesIO()
    Image.new("1", (side, side)).save(b, "PNG")
    with pytest.raises(InvalidImage):
        _decode_square(b.getvalue())


def test_decode_48_megapixel_jpeg_ok():
    from app.images import _decode_square

    assert _decode_square(jpeg(8000, 6000)).size == (512, 512)


def test_decode_is_serialized(monkeypatch, tmp_path):
    import threading
    import time

    import app.images as images

    monkeypatch.setattr(settings, "media_dir", tmp_path)
    state = {"now": 0, "max": 0}
    guard = threading.Lock()
    real = images._decode_square

    def slow(data):
        with guard:
            state["now"] += 1
            state["max"] = max(state["max"], state["now"])
        time.sleep(0.15)
        try:
            return real(data)
        finally:
            with guard:
                state["now"] -= 1

    monkeypatch.setattr(images, "_decode_square", slow)
    data = jpeg(600, 600)
    threads = [threading.Thread(target=images.save_avatar, args=(i, data)) for i in range(1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert state["max"] == 1
    assert len(list(tmp_path.glob("avatars/*/*.webp"))) == 4


def test_decode_transparent_png_gets_white_background():
    from app.images import _decode_square

    b = BytesIO()
    Image.new("RGBA", (800, 600), (255, 0, 0, 0)).save(b, "PNG")  # полностью прозрачная
    img = _decode_square(b.getvalue())
    assert img.mode == "RGB" and img.getpixel((256, 256)) == (255, 255, 255)

    b = BytesIO()
    p = Image.new("P", (800, 600), 0)
    p.putpalette([255, 0, 0] + [0] * 765)
    p.save(b, "PNG", transparency=0)  # прозрачность через палитру
    assert _decode_square(b.getvalue()).getpixel((256, 256)) == (255, 255, 255)


def test_decode_respects_exif_orientation():
    from app.images import _decode_square

    src = Image.new("RGB", (300, 200), "blue")
    src.paste("red", (0, 0, 150, 200))  # слева красный, справа синий
    exif = Image.Exif()
    exif[0x0112] = 6  # показывать повёрнутым на 90° по часовой: левая половина уходит вверх
    b = BytesIO()
    src.save(b, "JPEG", quality=95, exif=exif)
    img = _decode_square(b.getvalue())
    top, bottom = img.getpixel((256, 40)), img.getpixel((256, 470))
    assert top[0] > 200 and top[2] < 60 and bottom[2] > 200 and bottom[0] < 60
