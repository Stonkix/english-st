import json
import re
from datetime import datetime

from sqlalchemy import select

from app.daily import (
    NoAttemptsLeft,
    NothingToday,
    check_pair,
    settings_for,
    start_or_resume,
    submit_answer,
    todays_status,
)
from app.models import Attempt, AttemptAnswer, GroupPool, Pool, Question, User
from app.questions import pair_ids
from app.timeutil import moscow_today
from tests.factories import login_as, make_group, make_user


def setup_class(db, questions=None):
    """Учитель, группа с пулом, ученик (вошёл). questions — список (type, payload)."""
    teacher = make_user(db, role="teacher")
    group = make_group(db, teacher)
    student = make_user(db, group=group, login="kid")
    pool = Pool(owner_id=teacher.id, title="Пул", description="d", level="A1")
    db.add(pool)
    db.flush()
    if questions is None:
        questions = [("text_input", {"accepted": [f"word{i}"]}) for i in range(3)]
    rows = [Question(owner_id=teacher.id, pool_id=pool.id, type=t, prompt=f"q{i}", payload=p) for i, (t, p) in enumerate(questions)]
    db.add_all(rows)
    db.add(GroupPool(group_id=group.id, pool_id=pool.id))
    db.commit()
    return teacher, group, student, rows


def player_data(client) -> dict:
    r = client.get("/daily/play", follow_redirects=False)
    assert r.status_code == 200
    m = re.search(r'<script type="application/json" id="player-data">(.*?)</script>', r.text, re.S)
    assert m
    return json.loads(m.group(1))


def right_answer(db, q: dict) -> dict:
    """Правильный ответ на вопрос из player-data (берём из БД — так, как это сделал бы знающий ученик)."""
    row = db.get(Question, q["id"])
    if row.type == "text_input":
        return {"text": row.payload["accepted"][0]}
    if row.type in ("choice_text", "choice_image"):
        return {"option": row.payload["correct"]}
    return {}


def answer(client, data, q, ans):
    return client.post("/daily/answer", json={"attempt_id": data["attempt_id"], "question_id": q["id"], "answer": ans})


def test_full_flow(client, db):
    _, _, student, _ = setup_class(db, [
        ("text_input", {"accepted": ["apple"]}),
        ("choice_text", {"options": ["cat", "dog"], "correct": 1}),
        ("word_bank", {"answer": "I like cats", "distractors": ["dogs"]}),
    ])
    login_as(client, "kid")
    home = client.get("/")
    assert "Задание на сегодня" in home.text and "Начать" in home.text

    r = client.post("/daily/start", follow_redirects=False)
    assert (r.status_code, r.headers["location"]) == (303, "/daily/play")
    page = client.get("/daily/play").text
    assert "/static/js/player.js" in page
    data = player_data(client)
    raw = json.dumps(data)
    assert "accepted" not in raw and '"correct"' not in raw and "answer" not in raw  # ответов в плеере нет
    assert data["index"] == 0 and len(data["questions"]) == 3

    for i, q in enumerate(data["questions"]):
        if q["type"] == "word_bank":
            by_word = {t["word"]: t["id"] for t in q["tiles"]}
            ans = {"tiles": [by_word[w] for w in ("I", "like", "cats")]}
        else:
            ans = right_answer(db, q)
        res = answer(client, data, q, ans).json()
        assert res["correct"] is True and res["typo"] is False
        assert res["finished"] is (i == 2) and res["next_index"] == i + 1

    attempt = db.scalar(select(Attempt))
    assert attempt.finished_at is not None and (attempt.correct_count, attempt.total_count) == (3, 3)
    db.refresh(student)
    assert student.current_streak == 1 and student.last_completed_date == moscow_today()
    result = client.get(f"/daily/result/{attempt.id}")
    assert "Верно 3 из 3" in result.text and "Ещё раз" in result.text
    assert client.get("/daily/play", follow_redirects=False).headers["location"] == "/"  # активной попытки нет
    assert "Пройдено 3 из 3" in client.get("/").text


def test_answer_snapshot_and_typo(client, db):
    setup_class(db, [("text_input", {"accepted": ["apple"]}), ("choice_text", {"options": ["cat", "dog"], "correct": 1})])
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    first = data["questions"][0]
    res = answer(client, data, first, {"text": "aple"} if first["type"] == "text_input" else {"option": 0}).json()
    row = db.scalar(select(AttemptAnswer))
    if first["type"] == "text_input":
        assert (res["correct"], res["typo"], res["correct_answer"]) == (True, True, "apple")
        assert row.answer == {"raw": {"text": "aple"}, "shown": "aple", "correct": "apple"} and row.had_typo
    else:
        assert (res["correct"], res["correct_answer"]) == (False, "dog")
        assert row.answer == {"raw": {"option": 0}, "shown": "cat", "correct": "dog"} and not row.is_correct
    # учитель потом правит вопрос — история не меняется
    q = db.get(Question, first["id"])
    q.payload = {"accepted": ["pear"]} if q.type == "text_input" else {"options": ["x", "y"], "correct": 0}
    db.commit()
    db.expire_all()
    assert db.scalar(select(AttemptAnswer)).answer["correct"] in ("apple", "dog")


def test_resume_after_reload(client, db):
    setup_class(db)
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    answer(client, data, data["questions"][0], right_answer(db, data["questions"][0]))
    again = player_data(client)
    assert again["index"] == 1
    assert [q["id"] for q in again["questions"]] == [q["id"] for q in data["questions"]]
    assert again["attempt_id"] == data["attempt_id"]
    # «Начать» ещё раз (вторая вкладка) возвращает ту же попытку
    client.post("/daily/start")
    assert len(db.scalars(select(Attempt)).all()) == 1
    assert "Продолжить" in client.get("/").text


def test_double_submit_counted_once(client, db):
    setup_class(db)
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    q = data["questions"][0]
    first = answer(client, data, q, {"text": "wrong"}).json()
    second = answer(client, data, q, right_answer(db, q)).json()  # повтор (даже с другим ответом) не перезаписывает
    assert first == second and first["correct"] is False
    assert len(db.scalars(select(AttemptAnswer)).all()) == 1


def test_concurrent_duplicate_returns_stored(db, monkeypatch):
    """Два одновременных ответа: второй упирается в первичный ключ — без 500, тот же результат."""
    import app.daily as daily

    _, group, student, rows = setup_class(db, [("text_input", {"accepted": ["a"]}), ("text_input", {"accepted": ["b"]})])
    settings_for(db, group.id).shuffle_questions = False
    db.commit()
    attempt = start_or_resume(db, student)
    first = submit_answer(db, attempt, rows[0].id, {"text": "a"})
    real = daily._current

    def stale(db_, attempt_):  # как будто параллельный запрос прочитал состояние до первой записи
        live, _, _ = real(db_, attempt_)
        return live, {}, live[0]

    monkeypatch.setattr(daily, "_current", stale)
    again = submit_answer(db, attempt, rows[0].id, {"text": "zzz"})
    assert again == first and again["next_index"] == 1
    assert len(db.scalars(select(AttemptAnswer)).all()) == 1


def test_double_submit_on_last_question(client, db):
    setup_class(db, [("text_input", {"accepted": ["a"]})])
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    q = data["questions"][0]
    first = answer(client, data, q, {"text": "a"})
    second = answer(client, data, q, {"text": "a"})
    assert second.status_code == 200 and first.json() == second.json() and first.json()["finished"] is True
    assert db.scalar(select(User).where(User.login == "kid")).current_streak == 1


def test_wrong_order_and_bad_body(client, db):
    setup_class(db)
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    r = answer(client, data, data["questions"][2], {"text": "x"})  # перескочить вперёд нельзя
    assert r.status_code == 409 and r.headers["content-type"].startswith("application/json")
    r = client.post("/daily/answer", json={"attempt_id": "x"})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    r = client.post("/daily/answer", json={"attempt_id": 2**40, "question_id": 1, "answer": {}})
    assert r.status_code == 422 and r.headers["content-type"].startswith("application/json")
    assert db.scalar(select(AttemptAnswer)) is None


def test_attempt_limit(client, db):
    _, group, _, _ = setup_class(db, [("text_input", {"accepted": ["a"]})])
    settings_for(db, group.id).max_attempts = 1
    db.commit()
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    answer(client, data, data["questions"][0], {"text": "a"})
    r = client.post("/daily/start")
    assert "Попытки на сегодня закончились" in r.text
    assert len(db.scalars(select(Attempt)).all()) == 1
    assert "Ещё раз" not in client.get(f"/daily/result/{data['attempt_id']}").text
    finished = client.post("/daily/answer", json={"attempt_id": data["attempt_id"], "question_id": data["questions"][0]["id"] + 1000, "answer": {}})
    assert finished.status_code == 409


def test_second_attempt_same_questions(client, db):
    teacher, group, student, rows = setup_class(db, [("text_input", {"accepted": [f"w{i}"]}) for i in range(8)])
    login_as(client, "kid")
    client.post("/daily/start")
    first = player_data(client)
    for q in first["questions"]:
        answer(client, first, q, {"text": "nope"})
    # учитель добавил вопросов в пул — набор на день всё равно тот же
    db.add(Question(owner_id=teacher.id, pool_id=rows[0].pool_id, type="text_input", prompt="new", payload={"accepted": ["n"]}))
    db.commit()
    client.post("/daily/start")
    second = player_data(client)
    assert second["attempt_id"] != first["attempt_id"] and second["index"] == 0
    assert sorted(q["id"] for q in second["questions"]) == sorted(q["id"] for q in first["questions"])
    assert len(second["questions"]) == 5
    db.refresh(student)
    assert student.current_streak == 1  # серия за день — один раз


def test_question_deleted_mid_attempt(client, db):
    setup_class(db)
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    hidden, gone = data["questions"][1], data["questions"][2]
    db.get(Question, hidden["id"]).is_active = False  # скрыт учителем — всё равно играется
    db.delete(db.get(Question, gone["id"]))  # удалён совсем (ответов не было) — пропускается
    db.commit()
    data = player_data(client)
    assert [q["id"] for q in data["questions"]] == [data["questions"][0]["id"], hidden["id"]]
    answer(client, data, data["questions"][0], right_answer(db, data["questions"][0]))
    res = answer(client, data, data["questions"][1], right_answer(db, data["questions"][1])).json()
    assert res["finished"] is True and res["correct"] is True
    attempt = db.scalar(select(Attempt))
    db.refresh(attempt)
    assert (attempt.correct_count, attempt.total_count) == (2, 2)
    assert "Верно 2 из 2" in client.get(f"/daily/result/{attempt.id}").text


def test_last_question_deleted_finishes_attempt(client, db):
    setup_class(db, [("text_input", {"accepted": ["a"]}), ("text_input", {"accepted": ["b"]})])
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    answer(client, data, data["questions"][0], right_answer(db, data["questions"][0]))
    db.delete(db.get(Question, data["questions"][1]["id"]))
    db.commit()
    r = client.get("/daily/play", follow_redirects=False)
    assert r.headers["location"] == f"/daily/result/{data['attempt_id']}"
    attempt = db.get(Attempt, data["attempt_id"])
    db.refresh(attempt)
    assert attempt.finished_at is not None and (attempt.correct_count, attempt.total_count) == (1, 1)


def _pairs_question():
    return [("match_pairs", {"pairs": [["cat", "кошка"], ["dog", "собака"], ["sun", "солнце"]]})]


def test_match_pairs_mistake_makes_wrong(client, db):
    _, _, student, rows = setup_class(db, _pairs_question())
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    q = data["questions"][0]
    left, right = pair_ids(rows[0], f"{student.id}:{moscow_today().isoformat()}")
    send = lambda lt, rt: client.post("/daily/pair", json={"attempt_id": data["attempt_id"], "question_id": q["id"], "left": lt, "right": rt}).json()  # noqa: E731
    assert send(left[0], right[1]) == {"ok": False}
    for lt, rt in zip(left, right):
        assert send(lt, rt) == {"ok": True}
    res = answer(client, data, q, {}).json()
    assert res["correct"] is False and res["finished"] is True
    assert "кошка" in res["correct_answer"]
    assert db.scalar(select(Attempt)).pair_mistakes == {str(q["id"]): 1}


def test_match_pairs_clean_is_right(client, db):
    _, _, student, rows = setup_class(db, _pairs_question())
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    q = data["questions"][0]
    left, right = pair_ids(rows[0], f"{student.id}:{moscow_today().isoformat()}")
    for lt, rt in zip(left, right):
        r = client.post("/daily/pair", json={"attempt_id": data["attempt_id"], "question_id": q["id"], "left": lt, "right": rt})
        assert r.json() == {"ok": True}
    assert answer(client, data, q, {}).json()["correct"] is True


def test_foreign_attempt_404(client, db):
    _, group, _, _ = setup_class(db)
    make_user(db, group=group, login="other")
    login_as(client, "kid")
    client.post("/daily/start")
    data = player_data(client)
    q = data["questions"][0]
    login_as(client, "other")
    r = answer(client, data, q, {"text": "x"})
    assert r.status_code == 404 and r.headers["content-type"].startswith("application/json")
    r = client.post("/daily/pair", json={"attempt_id": data["attempt_id"], "question_id": q["id"], "left": 0, "right": 0})
    assert r.status_code == 404
    assert client.get(f"/daily/result/{data['attempt_id']}").status_code == 404
    assert db.scalar(select(AttemptAnswer)) is None


def test_day_rollover_new_set(db):
    _, _, student, _ = setup_class(db, [("text_input", {"accepted": [f"w{i}"]}) for i in range(12)])
    late = datetime(2026, 10, 26, 20, 59)  # 23:59 МСК 26.10
    early = datetime(2026, 10, 26, 21, 1)  # 00:01 МСК 27.10
    first = start_or_resume(db, student, now=late)
    assert start_or_resume(db, student, now=late).id == first.id  # тот же день — та же попытка
    second = start_or_resume(db, student, now=early)  # вчерашняя незавершённая не мешает
    assert (first.date.isoformat(), second.date.isoformat()) == ("2026-10-26", "2026-10-27")
    assert second.id != first.id and set(second.question_ids) != set(first.question_ids)
    assert todays_status(db, student, now=early)["state"] == "in_progress"


def test_status_and_errors_without_tasks(client, db):
    teacher = make_user(db, role="teacher")
    group = make_group(db, teacher)
    student = make_user(db, group=group, login="kid")
    assert todays_status(db, student)["state"] == "none"
    try:
        start_or_resume(db, student)
        raise AssertionError("ожидали NothingToday")
    except NothingToday:
        pass
    login_as(client, "kid")
    assert "Сегодня заданий нет" in client.get("/").text
    r = client.post("/daily/start", follow_redirects=False)
    assert r.headers["location"] == "/"


def test_service_functions(db):
    _, group, student, rows = setup_class(db, _pairs_question() + [("text_input", {"accepted": ["a"]})])
    settings_for(db, group.id).shuffle_questions = False
    settings_for(db, group.id).max_attempts = 1
    db.commit()
    attempt = start_or_resume(db, student)
    assert attempt.question_ids == [rows[0].id, rows[1].id]
    try:
        submit_answer(db, attempt, rows[1].id, {"text": "a"})
        raise AssertionError("ожидали ValueError")
    except ValueError:
        pass
    assert check_pair(db, attempt, rows[0].id, 99, 99) is False
    res = submit_answer(db, attempt, rows[0].id, {})
    assert res["correct"] is False and res["next_index"] == 1
    res = submit_answer(db, attempt, rows[1].id, {"text": "a"})
    assert res["finished"] is True
    status = todays_status(db, student)
    assert (status["state"], status["attempts_used"], status["max_attempts"], status["best"]) == ("done", 1, 1, (1, 2))
    try:
        start_or_resume(db, student)
        raise AssertionError("ожидали NoAttemptsLeft")
    except NoAttemptsLeft:
        pass


def test_teacher_cannot_play(client, db):
    teacher, _, _, _ = setup_class(db)
    login_as(client, teacher.login)
    assert client.post("/daily/start").status_code == 403
    assert client.get("/daily/play").status_code == 403
    assert client.post("/daily/answer", json={"attempt_id": 1, "question_id": 1, "answer": {}}).status_code == 403
    assert client.get("/", follow_redirects=False).headers["location"] == "/teacher"
