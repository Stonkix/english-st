from app import stamps
from app.models import DailyReward, StampTransaction, StampType
from tests.factories import login_as, make_group, make_user
from tests.test_results import play, setup_group
from tests.test_stamps import _types


def test_teacher_gives_and_takes(client, db):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid", group=make_group(db, t))
    login_as(client, "teach")
    r = client.post(f"/teacher/students/{kid.id}/stamps",
                    data={"stamp_type_id": star.id, "amount": "5", "comment": "за урок"}, follow_redirects=False)
    assert r.status_code == 303 and stamps.balance_of(db, kid.id, star.id) == 5
    r = client.post(f"/teacher/students/{kid.id}/stamps/take", data={"stamp_type_id": star.id, "amount": "2", "reason": "  "})
    assert "Напишите, за что" in r.text and stamps.balance_of(db, kid.id, star.id) == 5
    r = client.post(f"/teacher/students/{kid.id}/stamps/take",
                    data={"stamp_type_id": star.id, "amount": "2", "reason": "За поведение на уроке"}, follow_redirects=False)
    assert r.status_code == 303 and stamps.balance_of(db, kid.id, star.id) == 3
    page = client.get(f"/teacher/students/{kid.id}").text
    assert "От учителя: за урок" in page and "Учитель забрал: За поведение на уроке" in page
    assert "Звезда" in client.get(f"/teacher/groups/{kid.group_id}").text


def test_student_sees_why_stamps_were_taken(client, db):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid", group=make_group(db, t))
    stamps.give(db, t, kid, gem.id, 3)
    gem.is_active = False  # выключенный вид тоже можно забрать, если он у ученика есть
    db.commit()
    stamps.take(db, t, kid, gem.id, 1, "За грубость")
    login_as(client, "kid")
    home = client.get("/").text
    assert "Учитель забрал штампики" in home and "За грубость" in home
    assert "Учитель забрал: За грубость" in client.get("/stamps").text
    assert stamps.balance_of(db, kid.id, gem.id) == 2


def test_old_penalties_leave_home_page(client, db):
    from datetime import timedelta
    from app.timeutil import utcnow
    star, _ = _types(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid", group=make_group(db, t))
    stamps.give(db, t, kid, star.id, 3)
    tx = stamps.take(db, t, kid, star.id, 1, "За опоздание")
    tx.created_at = utcnow() - timedelta(days=4)
    db.commit()
    login_as(client, "kid")
    assert "Учитель забрал штампики" not in client.get("/").text
    assert "За опоздание" in client.get("/stamps").text


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


# ---------- рисовки и обмен ----------


def _rgba_png() -> bytes:
    from io import BytesIO
    from PIL import Image
    buf = BytesIO()
    Image.new("RGBA", (80, 80), (255, 0, 0, 0)).save(buf, "PNG")
    return buf.getvalue()


def test_student_exchange_shows_drop(client, db):
    from tests.test_stamps import _kinds, _rule
    common, rare, legend = _kinds(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    stamps.give(db, t, kid, common.id, 12)
    login_as(client, "kid")
    page = client.get("/stamps").text
    assert "Обмен" in page and "Коллекция" in page and "Нужно 1 (Легендарный)" in page
    r = client.post("/stamps/exchange", data={"rule_id": _rule(db, common, rare).id, "times": "1"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/stamps?got=")
    assert "+1 Редкий!" in client.get(r.headers["location"]).text
    r = client.post("/stamps/exchange", data={"rule_id": _rule(db, common, rare).id, "times": "1"})
    assert r.status_code == 400 and "Не хватает" in r.text
    assert "+1" not in client.get(f"/stamps?got={10**30}").text.split("Обмен")[0]


def test_got_param_shows_only_own_credit(client, db):
    from tests.test_stamps import _kinds
    common, _, _ = _kinds(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    other = make_user(db, login="other")
    tx = stamps.give(db, t, other, common.id, 7)
    login_as(client, "kid")
    assert "+7 Обычный!" not in client.get(f"/stamps?got={tx.id}").text


def test_admin_variants(client, db):
    from tests.test_stamps import _kinds
    from app.models import StampVariant
    common, rare, legend = _kinds(db)
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    r = client.post(f"/school/stamps/{common.id}/variants", data={"name": "Котик"},
                    files={"image": ("cat.png", _rgba_png(), "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    art = db.query(StampVariant).filter(StampVariant.image.is_not(None)).one()
    assert art.name == "Котик" and art.image.startswith("stamps/")
    img = client.get(f"/media/{art.image}")
    from io import BytesIO
    from PIL import Image
    assert img.status_code == 200 and Image.open(BytesIO(img.content)).mode == "RGBA"  # прозрачность сохранилась
    assert "Загрузите картинку" in client.post(f"/school/stamps/{common.id}/variants", data={"icon": "../x"}).text
    client.post(f"/school/stamps/{rare.id}/variants", data={"icon": "rocket"})
    rocket = db.query(StampVariant).filter_by(icon="rocket").one()
    # выпавшую рисовку удалить нельзя, только скрыть
    kid = make_user(db, login="kid")
    tx = stamps.give(db, make_user(db, role="teacher", login="teach"), kid, rare.id, 1)
    tx.variant_id = rocket.id
    db.commit()
    assert "можно только скрыть" in client.post(f"/school/stamps/variants/{rocket.id}/delete").text
    client.post(f"/school/stamps/variants/{rocket.id}/toggle")
    db.refresh(rocket)
    assert not rocket.is_active
    art_id = art.id
    client.post(f"/school/stamps/variants/{art_id}/delete")
    db.expire_all()
    assert db.get(StampVariant, art_id) is None


def test_admin_rules(client, db):
    from tests.test_stamps import _kinds
    from app.models import ConversionRule
    common, rare, legend = _kinds(db)
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    r = client.post("/school/stamp-rules", data={"from_type_id": common.id, "from_amount": "25", "to_type_id": legend.id, "to_amount": "1"})
    assert "«Легендарный» нельзя получить обменом" in r.text
    before = db.query(ConversionRule).count()
    client.post("/school/stamp-rules", data={"from_type_id": rare.id, "from_amount": "2", "to_type_id": common.id, "to_amount": "21"})
    assert db.query(ConversionRule).count() == before + 1
    rule = db.query(ConversionRule).order_by(ConversionRule.id.desc()).first()
    client.post(f"/school/stamp-rules/{rule.id}/toggle")
    db.refresh(rule)
    assert not rule.is_active
    client.post(f"/school/stamp-rules/{rule.id}/delete")
    assert db.query(ConversionRule).count() == before


def test_take_into_minus_and_swearing_reason(client, db):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid", group=make_group(db, t))
    login_as(client, "teach")
    page = client.get(f"/teacher/students/{kid.id}", params={"take_reason": "За мат"}).text
    assert 'value="За мат"' in page and '<details class="card" id="take" open>' in page
    assert '<option value="За мат">' in page  # подсказка в списке причин
    r = client.post(f"/teacher/students/{kid.id}/stamps/take",
                    data={"stamp_type_id": gem.id, "amount": "2", "reason": "За мат"}, follow_redirects=False)
    assert r.status_code == 303 and stamps.balance_of(db, kid.id, gem.id) == -2
    assert "is-negative" in client.get(f"/teacher/students/{kid.id}").text
    client.post("/logout")
    login_as(client, "kid")
    home = client.get("/").text
    assert "За мат" in home and "is-negative" in home


def test_chat_gives_moderator_swear_reason(client, db):
    import json
    from app import chat
    star, _ = _types(db)
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t)
    make_user(db, login="kid", group=g)
    login_as(client, "teach")
    html = client.get(f"/chat/{chat.group_room(db, g.id).id}").text
    data = json.loads(html.split('id="chat-data">')[1].split("</script>")[0])
    assert data["swear_reason"] == "За мат" and data["moderator"]
