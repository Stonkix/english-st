from sqlalchemy import text

from datetime import timedelta

from app.daily import start_or_resume, submit_answer
from app.models import Attempt, GroupPool, Pool, Question, Test, TestQuestion
from app.timeutil import moscow_today, utcnow
from tests.factories import login_as, make_group, make_user


def setup_group(db, count=2):
    teacher = make_user(db, role="teacher", login="teach")
    group = make_group(db, teacher)
    pool = Pool(owner_id=teacher.id, title="Пул", description="d", level="A1")
    db.add(pool)
    db.flush()
    questions = [
        Question(owner_id=teacher.id, pool_id=pool.id, type="text_input", prompt=f"Вопрос {i}", payload={"accepted": [f"word{i}"]})
        for i in range(count)
    ]
    db.add_all(questions)
    db.add(GroupPool(group_id=group.id, pool_id=pool.id))
    db.commit()
    return teacher, group, questions


def play(db, student, texts):
    """Проходит попытку ученика, отвечая по очереди текстами; возвращает попытку."""
    attempt = start_or_resume(db, student)
    for qid, text in zip(attempt.question_ids, texts):
        submit_answer(db, attempt, qid, {"text": text})
    db.refresh(attempt)
    return attempt


def old_attempt(db, student, day, correct, total, finished=True):
    a = Attempt(
        student_id=student.id, date=day, source="pool", question_ids=[], total_count=total,
        correct_count=correct, finished_at=utcnow() if finished else None,
    )
    db.add(a)
    db.commit()
    return a


def test_day_results_statuses(client, db):
    _, group, qs = setup_group(db, 2)
    done = make_user(db, group=group, login="done", first_name="Анна", last_name="Аа")
    mid = make_user(db, group=group, login="mid", first_name="Борис", last_name="Бб")
    make_user(db, group=group, login="none", first_name="Вера", last_name="Вв")
    make_user(db, login="outsider", first_name="Чужой", last_name="Чч")  # без группы — не виден
    texts = {q.id: f"word{i}" for i, q in enumerate(qs)}
    first = start_or_resume(db, done)
    for n, qid in enumerate(first.question_ids):
        submit_answer(db, first, qid, {"text": "wrong" if n == 0 else texts[qid]})
    play(db, done, [texts[q] for q in start_or_resume(db, done).question_ids])  # вторая попытка
    start_or_resume(db, mid)

    login_as(client, "teach")
    page = client.get(f"/tasks/groups/{group.id}/results")
    assert page.status_code == 200
    html = page.text
    assert html.index("Анна") < html.index("Борис") < html.index("Вера")
    assert "Чужой" not in html
    assert "пройдено" in html and "в процессе" in html and "не начинал" in html
    assert "Лучший результат: 2 из 2" in html and "первая попытка: 1 из 2" in html
    assert "попыток: 2" in html
    assert f"/tasks/students/{done.id}/results" in html
    assert "Позже" not in html  # сегодня — «вперёд» скрыт

    # на странице группы — блок «Сегодня»
    assert "Сегодня: прошли 1 из 3" in client.get(f"/teacher/groups/{group.id}").text


def test_attempt_detail_shows_answers(client, db):
    teacher, group, qs = setup_group(db, 2)
    kid = make_user(db, group=group, login="kid")
    attempt = play(db, kid, ["oops", "word1"])
    # учитель правит и удаляет вопросы после прохождения
    first_id, second_id = attempt.question_ids
    first = db.get(Question, first_id)
    first.prompt = "Новая формулировка"
    first.payload = {"accepted": ["changed"]}
    db.execute(text("PRAGMA foreign_keys=OFF"))  # приложение вопросы с ответами только скрывает; здесь строка пропала совсем
    db.execute(text("DELETE FROM questions WHERE id = :i"), {"i": second_id})
    db.commit()
    db.execute(text("PRAGMA foreign_keys=ON"))

    login_as(client, "teach")
    html = client.get(f"/tasks/students/{kid.id}/results").text
    assert "Новая формулировка" in html
    assert "Вопрос удалён" in html
    assert "oops" in html  # ответ ученика из снимка
    assert "неверно" in html and "верно" in html
    assert "Правильный ответ:" in html and "changed" not in html
    assert "Пул вопросов" in html and "1 из 2" in html
    assert "<script" not in html.split("</head>")[-1].replace('src="/static', "")  # без inline-скриптов


def test_history_window_and_test_source(client, db):
    teacher, group, qs = setup_group(db, 1)
    kid = make_user(db, group=group, login="kid")
    today = moscow_today()
    old_attempt(db, kid, today - timedelta(days=14), 1, 5)  # вне окна
    old_attempt(db, kid, today - timedelta(days=13), 2, 5)
    test = Test(owner_id=teacher.id, title="Мой тест", description="d")
    db.add(test)
    db.flush()
    db.add(TestQuestion(test_id=test.id, question_id=qs[0].id, position=0))
    recent = old_attempt(db, kid, today, 3, 4)
    recent.source, recent.test_id = "test", test.id
    db.commit()

    login_as(client, "teach")
    html = client.get(f"/tasks/students/{kid.id}/results").text
    assert "Тест «Мой тест»" in html and "3 из 4" in html and "2 из 5" in html and "1 из 5" not in html
    assert html.index("3 из 4") < html.index("2 из 5")  # новые сверху


def test_day_param_navigation(client, db):
    _, group, _ = setup_group(db, 1)
    kid = make_user(db, group=group, login="kid")
    today = moscow_today()
    yesterday = today - timedelta(days=1)
    old_attempt(db, kid, yesterday, 1, 1)
    login_as(client, "teach")
    html = client.get(f"/tasks/groups/{group.id}/results?day={yesterday.isoformat()}").text
    assert yesterday.strftime("%d.%m.%Y") in html and "Лучший результат: 1 из 1" in html
    assert f"day={today.isoformat()}" in html  # «Позже» ведёт на сегодня
    assert f"day={(yesterday - timedelta(days=1)).isoformat()}" in html


def test_day_param_invalid_falls_back_to_today(client, db):
    _, group, _ = setup_group(db, 1)
    login_as(client, "teach")
    today = moscow_today()
    future = (today + timedelta(days=3)).isoformat()
    for bad in ("garbage", "2026-13-45", future, ""):
        html = client.get(f"/tasks/groups/{group.id}/results", params={"day": bad}).text
        assert f"{today.strftime('%d.%m.%Y')} · сегодня" in html, bad


def test_foreign_results_404(client, db):
    _, group, _ = setup_group(db, 1)
    kid = make_user(db, group=group, login="kid")
    other = make_user(db, role="teacher", login="other")
    login_as(client, "other")
    assert client.get(f"/tasks/groups/{group.id}/results").status_code == 404
    assert client.get(f"/tasks/students/{kid.id}/results").status_code == 404
    assert client.get("/tasks/groups/999999/results").status_code == 404
    assert client.get("/tasks/students/0/results").status_code == 404
    # учитель как «ученик» — тоже 404
    assert client.get(f"/tasks/students/{other.id}/results").status_code == 404


def test_students_forbidden_admin_allowed(client, db):
    _, group, _ = setup_group(db, 1)
    kid = make_user(db, group=group, login="kid")
    make_user(db, role="admin", login="boss")
    login_as(client, "kid")
    assert client.get(f"/tasks/groups/{group.id}/results").status_code == 403
    assert client.get(f"/tasks/students/{kid.id}/results").status_code == 403
    client.cookies.clear()
    login_as(client, "boss")
    assert client.get(f"/tasks/groups/{group.id}/results").status_code == 200
    assert client.get(f"/tasks/students/{kid.id}/results").status_code == 200
