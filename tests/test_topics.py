from datetime import date

from app.daily import todays_source
from app.models import GroupTopicQuota, Pool, Topic
from tests.factories import login_as, make_group, make_user
from tests.test_daily import _topic_setup


def _topics(db):
    db.query(Topic).delete()
    grammar, vocab = Topic(name="Грамматика", sort_order=1), Topic(name="Лексика", sort_order=2)
    db.add_all([grammar, vocab])
    db.commit()
    return grammar, vocab


def test_pool_gets_topic(client, db):
    grammar, vocab = _topics(db)
    make_user(db, role="teacher", login="teach")
    login_as(client, "teach")
    data = {"title": "Времена", "description": "Present Simple", "level": "A1", "topic_id": str(grammar.id)}
    assert client.post("/tasks/pools", data=data, follow_redirects=False).status_code == 303
    pool = db.query(Pool).one()
    assert pool.topic_id == grammar.id
    assert "Грамматика" in client.get("/tasks").text and "Грамматика" in client.get(f"/tasks/pools/{pool.id}").text
    client.post(f"/tasks/pools/{pool.id}/edit", data={**data, "topic_id": ""})
    db.refresh(pool)
    assert pool.topic_id is None
    r = client.post("/tasks/pools", data={**data, "topic_id": "999999"})
    assert "Выберите тему из списка" in r.text


def test_group_topic_quotas(client, db):
    group, grammar, vocab, ids = _topic_setup(db, 6)
    login_as(client, "teach")
    page = client.get(f"/tasks/groups/{group.id}").text
    assert "Вопросы по темам" in page and "в пулах группы 6" in page
    r = client.post(f"/tasks/groups/{group.id}/topics", data={f"topic_{grammar.id}": "3", f"topic_{vocab.id}": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert {(q.topic_id, q.count) for q in db.query(GroupTopicQuota)} == {(grammar.id, 3), (vocab.id, 1)}
    assert "Сейчас в задании 4 вопросов" in client.get(f"/tasks/groups/{group.id}").text
    kid = make_user(db, login="kid", group=group)
    picked = set(todays_source(db, kid, date(2026, 10, 26)).question_ids)
    assert len(picked & ids["grammar"]) == 3 and len(picked & ids["vocab"]) == 1
    assert "от 0 до 30" in client.post(f"/tasks/groups/{group.id}/topics", data={f"topic_{grammar.id}": "-1"}).text
    assert "не больше 30" in client.post(f"/tasks/groups/{group.id}/topics",
                                          data={f"topic_{grammar.id}": "20", f"topic_{vocab.id}": "20"}).text
    client.post(f"/tasks/groups/{group.id}/topics", data={f"topic_{grammar.id}": "0", f"topic_{vocab.id}": "0"})
    assert db.query(GroupTopicQuota).count() == 0
    other = make_group(db, make_user(db, role="teacher", login="other"))
    assert client.post(f"/tasks/groups/{other.id}/topics", data={}).status_code == 404


def test_admin_topics(client, db):
    grammar, vocab = _topics(db)
    t = make_user(db, role="teacher", login="teach")
    pool = Pool(owner_id=t.id, title="p", description="d", level="A1", topic_id=grammar.id)
    db.add(pool)
    db.commit()
    login_as(client, "teach")
    assert client.get("/school/topics").status_code == 403
    client.post("/logout")
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    assert "пулов: 1" in client.get("/school/topics").text
    client.post("/school/topics", data={"name": "  Чтение "})
    assert db.query(Topic).filter_by(name="Чтение").count() == 1
    assert "Такая тема уже есть" in client.post("/school/topics", data={"name": "лексика"}).text
    client.post(f"/school/topics/{vocab.id}", data={"name": "Слова", "sort_order": "5"})
    db.refresh(vocab)
    assert (vocab.name, vocab.sort_order) == ("Слова", 5)
    client.post(f"/school/topics/{grammar.id}/delete")
    db.refresh(pool)
    assert pool.topic_id is None
