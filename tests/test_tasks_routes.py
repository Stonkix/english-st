import random
import shutil
from datetime import date, timedelta
from io import BytesIO

import pytest
from PIL import Image

from app.config import settings
from app.models import Assignment, Attempt, AttemptAnswer, DailySettings, GroupPool, Pool, Question, Test, TestQuestion
from app.timeutil import moscow_today
from tests.factories import login_as, make_group, make_user


@pytest.fixture(autouse=True)
def _clean_pictures():
    """Id пользователей в тестах повторяются, а файлы между тестами не стираются."""
    shutil.rmtree(settings.media_dir / "questions", ignore_errors=True)
    yield


def png(w=40, h=30, color="red"):
    b = BytesIO()
    Image.new("RGB", (w, h), color).save(b, "PNG")
    return b.getvalue()


def teacher(client, db, login="teach1"):
    t = make_user(db, role="teacher", login=login)
    login_as(client, login)
    return t


def make_pool(db, owner, title="Пул", level="A1"):
    pool = Pool(owner_id=owner.id, title=title, description="Описание", level=level)
    db.add(pool)
    db.commit()
    return pool


def pics(owner):
    folder = settings.media_dir / "questions" / str(owner.id)
    return sorted(folder.glob("*.webp")) if folder.exists() else []


def post_q(client, pool, qtype, data, files=None):
    return client.post(
        f"/tasks/pools/{pool.id}/questions", data={"type": qtype, **data}, files=files, follow_redirects=False
    )


# ---------- пулы ----------


def test_create_pool_requires_level_from_list(client, db):
    t = teacher(client, db)
    good = {"title": "Еда", "description": "Слова про еду", "level": "A1+"}
    for bad in ("", "Z9", "a1"):
        r = client.post("/tasks/pools", data={**good, "level": bad}, follow_redirects=False)
        assert r.status_code == 200 and "Выберите уровень из списка" in r.text
    assert db.query(Pool).count() == 0
    r = client.post("/tasks/pools", data=good, follow_redirects=False)
    pool = db.query(Pool).one()
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/pools/{pool.id}"
    assert (pool.owner_id, pool.level) == (t.id, "A1+")


def test_pool_field_limits(client, db):
    teacher(client, db)
    ok = {"title": "Еда", "description": "Слова", "level": "A1"}
    for field, value in (("title", " "), ("title", "я" * 81), ("description", ""), ("description", "я" * 501)):
        r = client.post("/tasks/pools", data={**ok, field: value})
        assert r.status_code == 200 and 'role="alert"' in r.text, (field, len(value))
    assert db.query(Pool).count() == 0


def test_tasks_page_lists_own_pools_and_edit(client, db):
    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    make_pool(db, t, title="Мой пул")
    make_pool(db, other, title="Чужой пул")
    r = client.get("/tasks")
    assert "Мой пул" in r.text and "Чужой пул" not in r.text
    pool = db.query(Pool).filter_by(title="Мой пул").one()
    r = client.post(f"/tasks/pools/{pool.id}/edit", data={"title": "Новое", "description": "d", "level": "B2"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(pool)
    assert (pool.title, pool.level) == ("Новое", "B2")
    r = client.post(f"/tasks/pools/{pool.id}/edit", data={"title": "x", "description": "d", "level": "bad"})
    assert "Выберите уровень из списка" in r.text


def test_pool_foreign_404(client, db):
    other = make_user(db, role="teacher", login="teach2")
    pool = make_pool(db, other)
    q = Question(owner_id=other.id, pool_id=pool.id, type="text_input", prompt="Q", payload={"accepted": ["a"]})
    db.add(q)
    db.commit()
    teacher(client, db)
    base = f"/tasks/pools/{pool.id}"
    assert client.get(base).status_code == 404
    assert client.post(base + "/edit", data={"title": "x", "description": "d", "level": "A1"}).status_code == 404
    assert client.post(base + "/delete").status_code == 404
    assert client.get(base + "/questions/new?type=text_input").status_code == 404
    assert post_q(client, pool, "text_input", {"prompt": "p", "accepted": "a"}).status_code == 404
    assert client.get(f"/tasks/questions/{q.id}/edit").status_code == 404
    assert client.post(f"/tasks/questions/{q.id}", data={"prompt": "p", "accepted": "a"}).status_code == 404
    assert client.post(f"/tasks/questions/{q.id}/delete").status_code == 404
    assert client.get("/tasks/pools/0").status_code == 404
    client.post("/logout")
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    assert client.get(base).status_code == 200
    assert client.get(f"/tasks/questions/{q.id}/edit").status_code == 200


def test_delete_nonempty_pool_refused(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    pool_id = pool.id
    db.add(Question(owner_id=t.id, pool_id=pool_id, type="text_input", prompt="Q", payload={"accepted": ["a"]}))
    db.commit()
    r = client.post(f"/tasks/pools/{pool_id}/delete", follow_redirects=False)
    assert r.status_code == 200 and "Сначала удалите вопросы" in r.text
    assert db.get(Pool, pool_id) is not None
    q = db.query(Question).one()
    client.post(f"/tasks/questions/{q.id}/delete")
    r = client.post(f"/tasks/pools/{pool_id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/tasks"
    db.expire_all()
    assert db.get(Pool, pool_id) is None


def test_student_forbidden_on_tasks(client, db):
    t = make_user(db, role="teacher", login="teach1")
    pool = make_pool(db, t)
    g = make_group(db, t)
    make_user(db, login="kid", group=g)
    login_as(client, "kid")
    assert client.get("/tasks").status_code == 403
    assert client.get(f"/tasks/pools/{pool.id}").status_code == 403
    assert client.post("/tasks/pools", data={"title": "a", "description": "b", "level": "A1"}).status_code == 403
    assert post_q(client, pool, "text_input", {"prompt": "p", "accepted": "a"}).status_code == 403


def test_nav_has_tasks_for_teacher_and_admin_only(client, db):
    make_user(db, role="admin", login="adm")
    g = make_group(db, make_user(db, role="teacher", login="teach1"))
    make_user(db, login="kid", group=g)
    for who, shown in (("teach1", True), ("adm", True), ("kid", False)):
        login_as(client, who)
        assert ('href="/tasks"' in client.get("/profile").text) is shown, who
        client.post("/logout")


# ---------- вопросы ----------


def test_create_each_question_type(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    cases = {
        "choice_text": {"prompt": "Кот?", "options": ["cat", "dog", ""], "correct": "0"},
        "text_input": {"prompt": "Яблоко", "accepted": ["apple", "an apple"]},
        "word_bank": {"prompt": "Я люблю чай", "answer": "I like tea", "distractors": ["dog", ""]},
        "match_pairs": {"prompt": "Соедини", "left": ["cat", "dog", "", ""], "right": ["кот", "собака", "", ""]},
    }
    for qtype, data in cases.items():
        r = post_q(client, pool, qtype, data)
        assert r.status_code == 303, (qtype, r.text[:300])
    r = post_q(
        client, pool, "choice_image",
        {"prompt": "Где кот?", "captions": ["cat", "dog"], "correct": "1"},
        files={"option_file_0": ("a.png", png(), "image/png"), "option_file_1": ("b.png", png(color="blue"), "image/png")},
    )
    assert r.status_code == 303
    by_type = {q.type: q for q in db.query(Question).all()}
    assert set(by_type) == set(cases) | {"choice_image"}
    assert all(q.owner_id == t.id and q.pool_id == pool.id for q in by_type.values())
    assert by_type["choice_text"].payload == {"options": ["cat", "dog"], "correct": 0}
    assert by_type["text_input"].payload == {"accepted": ["apple", "an apple"]}
    assert by_type["word_bank"].payload == {"answer": "I like tea", "distractors": ["dog"]}
    assert by_type["match_pairs"].payload == {"pairs": [["cat", "кот"], ["dog", "собака"]]}
    options = by_type["choice_image"].payload["options"]
    assert by_type["choice_image"].payload["correct"] == 1 and [o["caption"] for o in options] == ["cat", "dog"]
    assert len(pics(t)) == 2 and all(o["image"].startswith(f"questions/{t.id}/") for o in options)
    assert "Кот?" in client.get(f"/tasks/pools/{pool.id}").text


def test_blank_rows_do_not_shift_correct_answer(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(client, pool, "choice_text", {"prompt": "Q", "options": ["", "cat", "", "dog"], "correct": "3"})
    assert db.query(Question).one().payload == {"options": ["cat", "dog"], "correct": 1}


def test_new_question_form_defaults(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    url = f"/tasks/pools/{pool.id}/questions/new"
    assert client.get(url + "?type=nope").status_code == 404
    assert client.get(url).status_code == 404
    r = client.get(url + "?type=choice_text")
    assert r.status_code == 200 and r.text.count('name="options"') == 4  # 3 строки + шаблон новой
    assert "editor.js" in r.text and "onclick" not in r.text


def test_payload_error_keeps_input(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    r = post_q(client, pool, "choice_text", {"prompt": "Мой вопрос", "options": ["cat", "dog"]})
    assert r.status_code == 200 and "Отметьте правильный вариант" in r.text
    assert "Мой вопрос" in r.text and 'value="cat"' in r.text and 'value="dog"' in r.text
    assert db.query(Question).count() == 0
    r = post_q(client, pool, "match_pairs", {"prompt": "P", "left": ["a", "b"], "right": ["x", ""]})
    assert "Правая часть пары" in r.text and 'value="x"' in r.text
    assert post_q(client, pool, "text_input", {"prompt": " ", "accepted": "a"}).status_code == 200
    assert db.query(Question).count() == 0


def test_payload_error_deletes_just_saved_pictures(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    r = post_q(
        client, pool, "choice_image",
        {"prompt": "Q", "captions": ["a", "b"]},  # правильный не отмечен
        files={"option_file_0": ("a.png", png(), "image/png"), "option_file_1": ("b.png", png(), "image/png")},
    )
    assert r.status_code == 200 and "Отметьте правильный вариант" in r.text
    assert pics(t) == [] and db.query(Question).count() == 0
    r = post_q(
        client, pool, "choice_image", {"prompt": "Q", "captions": ["a", "b"], "correct": "0"},
        files={"option_file_0": ("a.png", png(), "image/png"), "option_file_1": ("b.txt", b"not an image", "text/plain")},
    )
    assert r.status_code == 200 and "Не получилось открыть картинку" in r.text
    assert pics(t) == []


def test_choice_image_row_without_picture_refused(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    r = post_q(
        client, pool, "choice_image", {"prompt": "Q", "captions": ["a", "b"], "correct": "0"},
        files={"option_file_0": ("a.png", png(), "image/png")},
    )
    assert "Загрузите картинку для каждого варианта" in r.text
    assert pics(t) == []


@pytest.mark.parametrize(
    "path",
    [
        "questions/{other}/0123456789ab.webp",  # чужая папка
        "questions/{me}/0123456789ab.webp",  # нет файла
        "questions/{me}/../{other}/{name}",
        "avatars/{me}/{name}",
        "questions/{me}/{name}.png",
        "questions/{me}/{NAME}",  # верхний регистр
    ],
)
def test_foreign_or_missing_picture_path_rejected(client, db, path):
    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    pool = make_pool(db, t)
    # чужая настоящая картинка и своя настоящая
    from app.images import save_picture

    foreign = save_picture(other.id, png())
    name = foreign.rsplit("/", 1)[1]
    path = path.format(me=t.id, other=other.id, name=name, NAME=name.upper())
    own = save_picture(t.id, png())
    r = post_q(
        client, pool, "choice_image",
        {"prompt": "Q", "captions": ["a", "b"], "correct": "0", "option_images": [own, path]},
    )
    assert r.status_code == 200 and "Картинка не найдена, загрузите заново" in r.text
    assert db.query(Question).count() == 0
    r = post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a", "image_keep": path})
    assert "Картинка не найдена, загрузите заново" in r.text


def test_foreign_picture_path_real_file_of_other_owner_rejected(client, db):
    from app.images import save_picture

    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    pool = make_pool(db, t)
    foreign = save_picture(other.id, png())
    r = post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a", "image_keep": foreign})
    assert "Картинка не найдена, загрузите заново" in r.text
    assert db.query(Question).count() == 0


def test_prompt_picture_upload_keep_and_remove(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("p.png", png(), "image/png")})
    q = db.query(Question).one()
    first = q.image
    assert first and len(pics(t)) == 1
    url = f"/tasks/questions/{q.id}"
    # без изменений картинки: остаётся
    client.post(url, data={"prompt": "Q2", "accepted": "a", "image_keep": first})
    db.refresh(q)
    assert (q.prompt, q.image) == ("Q2", first)
    # замена: старый файл удалён
    client.post(url, data={"prompt": "Q2", "accepted": "a", "image_keep": first}, files={"image": ("p.png", png(color="blue"), "image/png")})
    db.refresh(q)
    assert q.image != first and len(pics(t)) == 1 and not (settings.media_dir / first).exists()
    # удаление
    client.post(url, data={"prompt": "Q2", "accepted": "a", "image_keep": q.image, "image_remove": "1"})
    db.refresh(q)
    assert q.image is None and pics(t) == []


def test_edit_choice_image_keeps_unchanged_and_replaces_one(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(
        client, pool, "choice_image", {"prompt": "Q", "captions": ["a", "b"], "correct": "0"},
        files={"option_file_0": ("a.png", png(), "image/png"), "option_file_1": ("b.png", png(color="blue"), "image/png")},
    )
    q = db.query(Question).one()
    old = [o["image"] for o in q.payload["options"]]
    page = client.get(f"/tasks/questions/{q.id}/edit")
    assert page.status_code == 200 and all(f"/media/{p}" in page.text for p in old)
    r = client.post(
        f"/tasks/questions/{q.id}",
        data={"prompt": "Q", "captions": ["a", "b"], "correct": "1", "option_images": old},
        files={"option_file_1": ("c.png", png(color="green"), "image/png")},
        follow_redirects=False,
    )
    assert r.status_code == 303
    db.refresh(q)
    new = [o["image"] for o in q.payload["options"]]
    assert new[0] == old[0] and new[1] != old[1] and q.payload["correct"] == 1
    assert (settings.media_dir / old[0]).exists() and not (settings.media_dir / old[1]).exists()
    assert len(pics(t)) == 2


def test_shared_picture_survives_until_last_reference(client, db):
    from app.images import save_picture

    t = teacher(client, db)
    pool = make_pool(db, t)
    shared = save_picture(t.id, png())
    for _ in range(2):
        post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a", "image_keep": shared})
    first, second = db.query(Question).order_by(Question.id).all()
    client.post(f"/tasks/questions/{first.id}/delete")
    assert (settings.media_dir / shared).exists()
    client.post(f"/tasks/questions/{second.id}/delete")
    assert not (settings.media_dir / shared).exists()


def test_delete_answered_question_hides_it(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(client, pool, "text_input", {"prompt": "Видимый", "accepted": "a"}, files={"image": ("p.png", png(), "image/png")})
    q = db.query(Question).one()
    kid = make_user(db, login="kid")
    attempt = Attempt(student_id=kid.id, date=date(2026, 1, 1), source="pool", question_ids=[q.id], total_count=1)
    db.add(attempt)
    db.commit()
    db.add(AttemptAnswer(attempt_id=attempt.id, question_id=q.id, answer={"text": "a"}, is_correct=True, had_typo=False))
    db.commit()
    r = client.post(f"/tasks/questions/{q.id}/delete", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/pools/{pool.id}"
    db.expire_all()
    q = db.get(Question, q.id)
    assert q is not None and q.is_active is False
    assert "Видимый" not in client.get(f"/tasks/pools/{pool.id}").text
    assert client.get(f"/tasks/questions/{q.id}/edit").status_code == 404
    assert len(pics(t)) == 1  # картинка нужна истории


def test_delete_unanswered_question_removes_row_and_images(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("p.png", png(), "image/png")})
    q_id = db.query(Question).one().id
    client.post(f"/tasks/questions/{q_id}/delete")
    db.expire_all()
    assert db.get(Question, q_id) is None and pics(t) == []


# ---------- картинки ----------


def one_image_question(client, db, owner_login="teach1"):
    t = teacher(client, db, owner_login)
    pool = make_pool(db, t)
    post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("p.png", png(), "image/png")})
    q = db.query(Question).filter_by(owner_id=t.id).one()
    client.post("/logout")
    return t, q, f"/media/{q.image}"


def test_question_image_visibility(client, db):
    t, q, url = one_image_question(client, db)
    t2 = make_user(db, role="teacher", login="teach2")
    g, g2 = make_group(db, t), make_group(db, t2)
    make_user(db, login="pupil-a", group=g)
    make_user(db, login="pupil-x", group=g2)
    make_user(db, role="admin", login="adm")

    anon = client.get(url, follow_redirects=False)
    assert anon.status_code == 303 and anon.headers["location"] == "/login"
    for who, code in (("teach1", 200), ("pupil-a", 200), ("adm", 200), ("pupil-x", 404), ("teach2", 404)):
        login_as(client, who)
        r = client.get(url)
        assert r.status_code == code, who
        if code == 200:
            assert r.headers["content-type"] == "image/webp" and r.headers["cache-control"] == "private, max-age=86400"
        client.post("/logout")


def test_question_image_only_if_referenced(client, db):
    t, q, url = one_image_question(client, db)
    login_as(client, "teach1")
    assert client.get(url).status_code == 200
    q.image = None
    db.commit()
    assert client.get(url).status_code == 404  # файл есть, но вопросы на него не ссылаются
    assert client.get(f"/media/questions/{t.id}/0123456789ab.webp").status_code == 404
    assert client.get(f"/media/questions/{t.id}/..%2fx.webp").status_code == 404
    assert client.get(f"/media/questions/007/{q.id}.webp").status_code == 404


def test_choice_image_option_images_are_served(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    post_q(
        client, pool, "choice_image", {"prompt": "Q", "captions": ["a", "b"], "correct": "0"},
        files={"option_file_0": ("a.png", png(), "image/png"), "option_file_1": ("b.png", png(), "image/png")},
    )
    q = db.query(Question).one()
    for o in q.payload["options"]:
        assert client.get("/media/" + o["image"]).status_code == 200


def test_question_image_prod_uses_x_accel(client, db, monkeypatch):
    t, q, url = one_image_question(client, db)
    login_as(client, "teach1")
    monkeypatch.setattr(settings, "debug", False)
    r = client.get(url)
    assert r.status_code == 200 and r.content == b"" and r.headers["x-accel-redirect"] == f"/_protected/{q.image}"


def test_question_image_size(client, db):
    rng = random.Random(1)
    noisy = Image.frombytes("RGB", (3000, 2000), rng.randbytes(3000 * 2000 * 3))  # шум: плохо сжимается
    exif = Image.Exif()
    exif[0x010F] = "SecretCamera"
    buf = BytesIO()
    noisy.save(buf, "JPEG", quality=95, exif=exif)
    assert len(buf.getvalue()) < 10 * 1024 * 1024
    t = teacher(client, db)
    pool = make_pool(db, t)
    r = post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("big.jpg", buf.getvalue(), "image/jpeg")})
    assert r.status_code == 303
    path = settings.media_dir / db.query(Question).one().image
    data = path.read_bytes()
    assert len(data) <= 400 * 1024
    out = Image.open(BytesIO(data))
    assert out.format == "WEBP" and max(out.size) == 1280 and out.size == (1280, 853)
    assert not out.getexif() and b"SecretCamera" not in data and b"EXIF" not in data


def test_small_picture_not_upscaled_and_transparency_white(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    b = BytesIO()
    Image.new("RGBA", (50, 20), (0, 0, 0, 0)).save(b, "PNG")
    post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("t.png", b.getvalue(), "image/png")})
    out = Image.open(settings.media_dir / db.query(Question).one().image).convert("RGB")
    assert out.size == (50, 20) and min(out.getpixel((10, 10))) > 240


def test_question_post_sets_no_unbounded_upload(client, db):
    from app.images import MAX_UPLOAD_BYTES

    t = teacher(client, db)
    pool = make_pool(db, t)
    r = post_q(client, pool, "text_input", {"prompt": "Q", "accepted": "a"}, files={"image": ("big.png", b"0" * (MAX_UPLOAD_BYTES + 1), "image/png")})
    assert r.status_code == 200 and "Не получилось открыть картинку" in r.text and pics(t) == []


# ---------- тесты ----------


def make_question(db, owner, pool=None, prompt="Вопрос"):
    q = Question(owner_id=owner.id, pool_id=pool.id if pool else None, type="text_input", prompt=prompt, payload={"accepted": ["cat"]})
    db.add(q)
    db.commit()
    return q


def make_test(db, owner, title="Тест", questions=()):
    test = Test(owner_id=owner.id, title=title, description="")
    db.add(test)
    db.flush()
    for pos, q in enumerate(questions):
        db.add(TestQuestion(test_id=test.id, question_id=q.id, position=pos))
    db.commit()
    return test


def order_of(db, test):
    return [r.question_id for r in db.query(TestQuestion).filter_by(test_id=test.id).order_by(TestQuestion.position)]


def test_create_test_and_list(client, db):
    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    make_test(db, other, "Чужой тест")
    r = client.post("/tasks/tests", data={"title": " ", "description": ""})
    assert r.status_code == 200 and 'role="alert"' in r.text
    r = client.post("/tasks/tests", data={"title": "Неделя 1", "description": "Повторение"}, follow_redirects=False)
    test = db.query(Test).filter_by(title="Неделя 1").one()
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/tests/{test.id}" and test.owner_id == t.id
    page = client.get("/tasks/tests").text
    assert "Неделя 1" in page and "Чужой тест" not in page


def test_foreign_test_404(client, db):
    teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    foreign = make_test(db, other)
    q = make_question(db, other)
    assert client.get(f"/tasks/tests/{foreign.id}").status_code == 404
    assert client.post(f"/tasks/tests/{foreign.id}/add", data={"question_id": q.id}).status_code == 404
    assert client.post(f"/tasks/tests/{foreign.id}/questions", data={"type": "text_input"}).status_code == 404


def test_add_existing_question_only_from_own_pools_once(client, db):
    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    mine = make_question(db, t, make_pool(db, t), "Моё")
    foreign = make_question(db, other, make_pool(db, other), "Чужое")
    loose = make_question(db, t, None, "Без пула")
    test = make_test(db, t)
    url = f"/tasks/tests/{test.id}/add"
    assert client.post(url, data={"question_id": mine.id}, follow_redirects=False).status_code == 303
    assert order_of(db, test) == [mine.id]
    r = client.post(url, data={"question_id": mine.id})
    assert r.status_code == 200 and "уже в тесте" in r.text and order_of(db, test) == [mine.id]
    assert client.post(url, data={"question_id": foreign.id}).status_code == 404
    assert client.post(url, data={"question_id": loose.id}).status_code == 404
    assert order_of(db, test) == [mine.id]


def test_test_page_shows_pool_questions_to_add(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t, title="Еда")
    make_question(db, t, pool, "Как будет яблоко")
    test = make_test(db, t)
    page = client.get(f"/tasks/tests/{test.id}").text
    assert "Еда" in page and "Как будет яблоко" not in page
    assert "Как будет яблоко" in client.get(f"/tasks/tests/{test.id}?pool={pool.id}").text
    assert client.get(f"/tasks/tests/{test.id}?pool=999999").status_code == 404


def test_create_question_directly_in_test(client, db):
    t = teacher(client, db)
    test = make_test(db, t)
    assert client.get(f"/tasks/tests/{test.id}/questions/new?type=text_input").status_code == 200
    assert client.get(f"/tasks/tests/{test.id}/questions/new?type=bad").status_code == 404
    r = client.post(
        f"/tasks/tests/{test.id}/questions",
        data={"type": "text_input", "prompt": "Кошка", "accepted": "cat"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/tests/{test.id}"
    q = db.query(Question).one()
    assert (q.pool_id, q.owner_id, q.prompt) == (None, t.id, "Кошка")
    assert order_of(db, test) == [q.id]
    bad = client.post(f"/tasks/tests/{test.id}/questions", data={"type": "text_input", "prompt": "", "accepted": "x"})
    assert bad.status_code == 200 and 'role="alert"' in bad.text and db.query(Question).count() == 1


def test_edit_test_question_returns_to_test(client, db):
    t = teacher(client, db)
    q = make_question(db, t, None, "Старый")
    test = make_test(db, t, questions=[q])
    page = client.get(f"/tasks/questions/{q.id}/edit")
    assert page.status_code == 200 and f"/tasks/tests/{test.id}" in page.text
    r = client.post(f"/tasks/questions/{q.id}", data={"prompt": "Новый", "accepted": "dog"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/tasks/tests/{test.id}"
    db.refresh(q)
    assert q.prompt == "Новый"


def test_move_and_remove_questions(client, db):
    t = teacher(client, db)
    pool = make_pool(db, t)
    a, b, c = (make_question(db, t, pool, n) for n in "abc")
    test = make_test(db, t, questions=[a, b, c])
    base = f"/tasks/tests/{test.id}/questions"
    client.post(f"{base}/{c.id}/move", data={"direction": "up"})
    assert order_of(db, test) == [a.id, c.id, b.id]
    client.post(f"{base}/{a.id}/move", data={"direction": "up"})  # крайний: без изменений
    assert order_of(db, test) == [a.id, c.id, b.id]
    client.post(f"{base}/{a.id}/move", data={"direction": "down"})
    assert order_of(db, test) == [c.id, a.id, b.id]
    assert client.post(f"{base}/{a.id}/move", data={"direction": "sideways"}).status_code == 404
    client.post(f"{base}/{c.id}/remove")
    assert order_of(db, test) == [a.id, b.id] and db.get(Question, c.id) is not None  # вопрос пула остаётся
    assert client.post(f"{base}/{c.id}/remove").status_code == 404


def test_remove_direct_question_deletes_it(client, db):
    t = teacher(client, db)
    q = make_question(db, t, None)
    test = make_test(db, t, questions=[q])
    client.post(f"/tasks/tests/{test.id}/questions/{q.id}/remove")
    assert order_of(db, test) == [] and db.query(Question).count() == 0


# ---------- задания группы ----------


def future(days=1):
    return (moscow_today() + timedelta(days=days)).isoformat()


def test_foreign_group_tasks_404(client, db):
    teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    group = make_group(db, other)
    test = make_test(db, other)
    assert client.get(f"/tasks/groups/{group.id}").status_code == 404
    for tail, data in (
        ("pools", {}),
        ("settings", {"question_count": 5, "max_attempts": 3}),
        ("assignments", {"test_id": test.id, "date": future(), "target": "group"}),
    ):
        assert client.post(f"/tasks/groups/{group.id}/{tail}", data=data).status_code == 404
    assert client.get("/tasks/groups/999999").status_code == 404
    assert db.query(DailySettings).count() == 0 and db.query(Assignment).count() == 0


def test_group_page_links_from_group(client, db):
    t = teacher(client, db)
    group = make_group(db, t)
    assert f"/tasks/groups/{group.id}" in client.get(f"/teacher/groups/{group.id}").text
    assert client.get(f"/tasks/groups/{group.id}").status_code == 200


def test_group_pools_only_teachers_own(client, db):
    t = teacher(client, db)
    other = make_user(db, role="teacher", login="teach2")
    group = make_group(db, t)
    mine, mine2, foreign = make_pool(db, t, "Мой 1"), make_pool(db, t, "Мой 2"), make_pool(db, other, "Чужой")
    page = client.get(f"/tasks/groups/{group.id}").text
    assert "Мой 1" in page and "Чужой" not in page
    r = client.post(f"/tasks/groups/{group.id}/pools", data={"pool_ids": [mine.id, foreign.id]}, follow_redirects=False)
    assert r.status_code == 303
    assert {g.pool_id for g in db.query(GroupPool)} == {mine.id}
    client.post(f"/tasks/groups/{group.id}/pools", data={"pool_ids": [mine2.id]})
    assert {g.pool_id for g in db.query(GroupPool)} == {mine2.id}
    client.post(f"/tasks/groups/{group.id}/pools", data={})
    assert db.query(GroupPool).count() == 0


def test_admin_sees_group_teachers_pools(client, db):
    t = make_user(db, role="teacher", login="teach1")
    group = make_group(db, t)
    mine = make_pool(db, t, "Пул учителя")
    admin = make_user(db, role="admin", login="boss")
    make_pool(db, admin, "Пул админа")
    login_as(client, "boss")
    page = client.get(f"/tasks/groups/{group.id}").text
    assert "Пул учителя" in page and "Пул админа" not in page
    client.post(f"/tasks/groups/{group.id}/pools", data={"pool_ids": [mine.id]})
    assert {g.pool_id for g in db.query(GroupPool)} == {mine.id}


def test_settings_validation(client, db):
    t = teacher(client, db)
    group = make_group(db, t)
    url = f"/tasks/groups/{group.id}/settings"
    for count, attempts, message in (
        ("0", "3", "Число вопросов — от 1 до 30"),
        ("31", "3", "Число вопросов — от 1 до 30"),
        ("abc", "3", "Число вопросов — от 1 до 30"),
        ("5", "0", "Попыток — от 1 до 10"),
        ("5", "11", "Попыток — от 1 до 10"),
        ("5", "", "Попыток — от 1 до 10"),
    ):
        r = client.post(url, data={"question_count": count, "max_attempts": attempts})
        assert r.status_code == 200 and message in r.text, (count, attempts)
    s = db.get(DailySettings, group.id)  # страница с ошибкой показала значения по умолчанию, ничего не сохранив
    assert (s.question_count, s.max_attempts) == (5, 3)
    r = client.post(url, data={"question_count": "30", "max_attempts": "10", "shuffle_options": "1"}, follow_redirects=False)
    assert r.status_code == 303
    db.refresh(s)
    assert (s.question_count, s.max_attempts, s.shuffle_questions, s.shuffle_options) == (30, 10, False, True)


def test_assignment_rules(client, db):
    t = teacher(client, db)
    group = make_group(db, t)
    kid = make_user(db, group=group)
    outsider = make_user(db, group=make_group(db, t, "Другая"))
    test = make_test(db, t)
    foreign_test = make_test(db, make_user(db, role="teacher", login="teach2"))
    url = f"/tasks/groups/{group.id}/assignments"
    today = moscow_today().isoformat()

    r = client.post(url, data={"test_id": test.id, "date": (moscow_today() - timedelta(days=1)).isoformat(), "target": "group"})
    assert r.status_code == 200 and "Дата уже прошла" in r.text
    for data in (
        {"test_id": foreign_test.id, "date": today, "target": "group"},
        {"test_id": test.id, "date": "не дата", "target": "group"},
        {"test_id": test.id, "date": today, "target": str(outsider.id)},
        {"test_id": test.id, "date": today, "target": "abc"},
    ):
        r = client.post(url, data=data)
        assert r.status_code == 200 and 'role="alert"' in r.text, data
    assert db.query(Assignment).count() == 0

    assert client.post(url, data={"test_id": test.id, "date": today, "target": "group"}, follow_redirects=False).status_code == 303
    assert client.post(url, data={"test_id": test.id, "date": future(), "target": str(kid.id)}, follow_redirects=False).status_code == 303
    group_a = db.query(Assignment).filter(Assignment.group_id == group.id).one()
    kid_a = db.query(Assignment).filter(Assignment.student_id == kid.id).one()
    assert (group_a.date, kid_a.date) == (moscow_today(), moscow_today() + timedelta(days=1))
    page = client.get(f"/tasks/groups/{group.id}").text
    assert kid.last_name in page and "Тест" in page

    client.post(f"{url}/{kid_a.id}/delete")
    assert db.query(Assignment).filter(Assignment.student_id == kid.id).count() == 0
    assert client.post(f"{url}/{kid_a.id}/delete").status_code == 404


def test_assignment_conflict_friendly(client, db):
    t = teacher(client, db)
    group = make_group(db, t)
    kid = make_user(db, group=group)
    test = make_test(db, t)
    url = f"/tasks/groups/{group.id}/assignments"
    for target in ("group", str(kid.id)):
        data = {"test_id": test.id, "date": future(2), "target": target}
        assert client.post(url, data=data, follow_redirects=False).status_code == 303
        r = client.post(url, data=data)
        assert r.status_code == 200 and "На эту дату уже назначен тест" in r.text
    assert db.query(Assignment).count() == 2


def test_cannot_delete_foreign_assignment(client, db):
    t = teacher(client, db)
    mine, other_group = make_group(db, t), make_group(db, t, "Другая")
    test = make_test(db, t)
    a = Assignment(test_id=test.id, group_id=other_group.id, date=moscow_today())
    db.add(a)
    db.commit()
    assert client.post(f"/tasks/groups/{mine.id}/assignments/{a.id}/delete").status_code == 404
    assert db.query(Assignment).count() == 1
