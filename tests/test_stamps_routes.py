from app import stamps
from app.models import DailyReward, StampTransaction, StampType
from tests.factories import login_as, make_group, make_user
from tests.test_results import play, setup_group
from tests.test_stamps import _types


def test_teacher_gives_and_takes(client, db):
    star, _ = _types(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid", group=make_group(db, t))
    login_as(client, "teach")
    r = client.post(f"/teacher/students/{kid.id}/stamps",
                    data={"stamp_type_id": star.id, "amount": "5", "action": "give", "comment": "за урок"},
                    follow_redirects=False)
    assert r.status_code == 303 and stamps.balance_of(db, kid.id, star.id) == 5
    r = client.post(f"/teacher/students/{kid.id}/stamps", data={"stamp_type_id": star.id, "amount": "9", "action": "take"})
    assert "Столько списать нельзя: у ученика 5" in r.text
    client.post(f"/teacher/students/{kid.id}/stamps", data={"stamp_type_id": star.id, "amount": "2", "action": "take"})
    assert stamps.balance_of(db, kid.id, star.id) == 3
    page = client.get(f"/teacher/students/{kid.id}").text
    assert "От учителя: за урок" in page
    assert "Звезда" in client.get(f"/teacher/groups/{kid.group_id}").text


def test_teacher_cannot_stamp_foreign_student(client, db):
    star, _ = _types(db)
    make_user(db, role="teacher", login="teach")
    other = make_user(db, role="teacher", login="other")
    kid = make_user(db, login="kid", group=make_group(db, other))
    login_as(client, "teach")
    r = client.post(f"/teacher/students/{kid.id}/stamps", data={"stamp_type_id": star.id, "amount": "5"})
    assert r.status_code == 404 and db.query(StampTransaction).count() == 0


def test_student_sees_balances_history_and_award(client, db):
    star, gem = _types(db)
    teacher, group, qs = setup_group(db, 1)
    kid = make_user(db, login="kid", group=group)
    db.add(DailyReward(group_id=group.id, stamp_type_id=gem.id, amount=2, min_score_percent=100))
    db.commit()
    stamps.give(db, teacher, kid, star.id, 4, "молодец")
    attempt = play(db, kid, ["word0"])
    login_as(client, "kid")
    home = client.get("/").text
    assert 'href="/stamps"' in home and ">4<" in home and ">2<" in home
    assert "+2" in client.get(f"/daily/result/{attempt.id}").text
    hist = client.get("/stamps").text
    assert "От учителя: молодец" in hist and "За задание" in hist
    client.post("/logout")
    login_as(client, "teach")
    assert client.get("/stamps", follow_redirects=False).status_code == 403


def test_rewards_crud(client, db):
    star, _ = _types(db)
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t)
    login_as(client, "teach")
    r = client.post(f"/tasks/groups/{g.id}/rewards", data={"stamp_type_id": star.id, "amount": "500", "min_score_percent": "0"})
    assert "Награда: выберите вид" in r.text
    client.post(f"/tasks/groups/{g.id}/rewards", data={"stamp_type_id": star.id, "amount": "3", "min_score_percent": "80"})
    reward = db.query(DailyReward).one()
    assert (reward.amount, reward.min_score_percent) == (3, 80)
    assert "от 80% верных" in client.get(f"/tasks/groups/{g.id}").text
    other = make_group(db, make_user(db, role="teacher", login="other"))
    assert client.post(f"/tasks/groups/{other.id}/rewards/{reward.id}/delete").status_code == 404
    assert client.post(f"/tasks/groups/{g.id}/rewards/{reward.id}/delete", follow_redirects=False).status_code == 303
    assert db.query(DailyReward).count() == 0


def test_admin_stamp_types(client, db):
    db.query(StampType).delete()
    db.commit()
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    r = client.post("/school/stamps", data={"name": "  Яблочко ", "icon": "apple", "color": "red", "sort_order": "1"},
                    follow_redirects=False)
    assert r.status_code == 303
    apple = db.query(StampType).one()
    assert apple.name == "Яблочко" and apple.is_active
    r = client.post("/school/stamps", data={"name": "Яблочко", "icon": "apple", "color": "red", "sort_order": "2"})
    assert "Такое название уже есть" in r.text
    r = client.post("/school/stamps", data={"name": "X", "icon": "../evil", "color": "red", "sort_order": "2"})
    assert "Выберите картинку и цвет" in r.text
    client.post(f"/school/stamps/{apple.id}", data={"name": "Яблоко", "icon": "apple", "color": "green", "sort_order": "1"})
    db.refresh(apple)
    assert apple.name == "Яблоко" and apple.color == "green" and not apple.is_active
    assert "Яблоко" in client.get("/school/stamps").text


def test_stamp_types_admin_only(client, db):
    make_user(db, role="teacher", login="teach")
    login_as(client, "teach")
    assert client.get("/school/stamps").status_code == 403


def test_huge_ids_in_forms_are_rejected_not_500(client, db):
    _types(db)
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t)
    kid = make_user(db, login="kid", group=g)
    login_as(client, "teach")
    big = str(10**30)
    assert "Выберите вид" in client.post(f"/teacher/students/{kid.id}/stamps", data={"stamp_type_id": big, "amount": "1"}).text
    assert "Награда: выберите вид" in client.post(f"/tasks/groups/{g.id}/rewards", data={"stamp_type_id": big, "amount": "1"}).text
    r = client.post(f"/shop/groups/{g.id}/items", data={"title": "x", "price_type_id": big, "price_amount": big})
    assert r.status_code == 400 and "Выберите вид штампиков для цены" in r.text
