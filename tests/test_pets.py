from datetime import date, timedelta

import pytest

from app import pets, stamps
from app.models import Pet, PetCare, PetConfig, PetFood, PetSpecies, StampType
from tests.factories import make_user, seed_filter

DAY = date(2026, 10, 26)


def _setup(db, balance=100):
    db.query(PetSpecies).delete()
    db.query(PetConfig).delete()
    db.query(StampType).delete()
    common = StampType(name="Обычный", icon="star", color="yellow", sort_order=1)
    db.add(common)
    db.flush()
    cat = PetSpecies(code="cat", name="Кошечка", price=15, sort_order=1)
    dog = PetSpecies(code="dog", name="Собачка", price=15, sort_order=2)
    db.add_all([cat, dog, PetConfig(id=1, price_type_id=common.id)])
    db.commit()
    t = make_user(db, role="teacher", login="teach")
    kid = make_user(db, login="kid")
    if balance:
        stamps.give(db, t, kid, common.id, balance)
    return kid, common, cat, dog


def test_buy_pet(db):
    kid, common, cat, dog = _setup(db, balance=20)
    pet = pets.buy_pet(db, kid, cat.id, "  Мурка ", today=DAY)
    assert (pet.name, pet.bought_on) == ("Мурка", DAY)
    assert stamps.balance_of(db, kid.id, common.id) == 5
    assert stamps.history(db, kid.id)[0].comment == "Кошечка «Мурка»"
    with pytest.raises(pets.PetError, match="уже есть"):
        pets.buy_pet(db, kid, cat.id, "Вторая")
    with pytest.raises(pets.PetError, match="нужно 15"):
        pets.buy_pet(db, kid, dog.id, "Шарик")
    assert db.query(Pet).count() == 1


def test_pet_name_rules(db):
    seed_filter(db)
    kid, common, cat, dog = _setup(db)
    for bad, msg in (("  ", "имя"), ("x" * 21, "не длиннее"), ("Сука", "не подойдёт")):
        with pytest.raises(pets.PetError, match=msg):
            pets.buy_pet(db, kid, cat.id, bad)
    dog.is_active = False
    db.commit()
    with pytest.raises(pets.PetError, match="нет в магазине"):
        pets.buy_pet(db, kid, dog.id, "Шарик")


def test_buy_food_pack_and_limit(db):
    kid, common, cat, dog = _setup(db, balance=10)
    assert pets.buy_food(db, kid, 3) == 30
    assert stamps.balance_of(db, kid.id, common.id) == 7
    with pytest.raises(pets.PetError, match="не больше 50 порций, у тебя 30"):
        pets.buy_food(db, kid, 3)
    assert pets.buy_food(db, kid, 2) == 50
    with pytest.raises(pets.PetError):
        pets.buy_food(db, kid, 0)


def test_feed_twice_and_clean_once(db):
    kid, common, cat, dog = _setup(db)
    pet = pets.buy_pet(db, kid, cat.id, "Мурка", today=DAY)
    with pytest.raises(pets.PetError, match="Корм закончился"):
        pets.feed(db, kid, pet.id, today=DAY)
    pets.buy_food(db, kid, 1)
    pets.feed(db, kid, pet.id, today=DAY)
    pets.feed(db, kid, pet.id, today=DAY)
    with pytest.raises(pets.PetError, match="уже сыт"):
        pets.feed(db, kid, pet.id, today=DAY)
    assert pets.food(db, kid.id) == 8
    pets.clean(db, kid, pet.id, today=DAY)
    with pytest.raises(pets.PetError, match="уже убрано"):
        pets.clean(db, kid, pet.id, today=DAY)
    status = pets.statuses(db, kid.id, today=DAY)[0]
    assert status.done and status.feedings == 2
    other = make_user(db, login="other")
    with pytest.raises(pets.PetError, match="не найден"):
        pets.feed(db, other, pet.id, today=DAY)


def _care(db, pet, day, feedings=2, cleaned=True):
    db.add(PetCare(pet_id=pet.id, day=day, feedings=feedings, cleaned=cleaned))
    db.commit()


def test_runs_away_after_three_days_without_care(db):
    kid, common, cat, dog = _setup(db)
    pet = pets.buy_pet(db, kid, cat.id, "Мурка", today=DAY)
    _care(db, pet, DAY + timedelta(days=1))  # полный уход
    _care(db, pet, DAY + timedelta(days=2), feedings=1)  # не докормили — день «без ухода»
    # 3-й и 4-й дни — ничего
    status = pets.statuses(db, kid.id, today=DAY + timedelta(days=4))[0]
    assert status.neglected == 2 and status.days_left == 1
    assert pets.check_runaways(db, kid.id, today=DAY + timedelta(days=5)) == [pet]
    db.refresh(pet)
    assert pet.ran_away_on == DAY + timedelta(days=5)
    assert pets.statuses(db, kid.id, today=DAY + timedelta(days=5)) == []
    with pytest.raises(pets.PetError, match="убежал"):
        pets.feed(db, kid, pet.id, today=DAY + timedelta(days=5))
    # можно завести снова
    assert pets.buy_pet(db, kid, cat.id, "Мурка 2", today=DAY + timedelta(days=5)).ran_away_on is None


def test_purchase_day_is_free_and_config_days(db):
    kid, common, cat, dog = _setup(db)
    pet = pets.buy_pet(db, kid, cat.id, "Мурка", today=DAY)
    assert pets.neglected_days(db, pet, DAY + timedelta(days=1), 3) == 0  # день покупки не считается
    db.get(PetConfig, 1).runaway_days = 1
    db.commit()
    assert pets.check_runaways(db, kid.id, today=DAY + timedelta(days=2)) == [pet]


def test_several_pets_share_food(db):
    kid, common, cat, dog = _setup(db)
    a = pets.buy_pet(db, kid, cat.id, "Мурка", today=DAY)
    b = pets.buy_pet(db, kid, dog.id, "Шарик", today=DAY)
    db.add(PetFood(student_id=kid.id, portions=3))
    db.commit()
    pets.feed(db, kid, a.id, today=DAY)
    pets.feed(db, kid, b.id, today=DAY)
    pets.feed(db, kid, b.id, today=DAY)
    with pytest.raises(pets.PetError, match="Корм закончился"):
        pets.feed(db, kid, a.id, today=DAY)
    assert [s.feedings for s in pets.statuses(db, kid.id, today=DAY)] == [1, 2]


# ---------- маршруты ----------

from tests.factories import login_as  # noqa: E402


def test_shop_buy_pet_and_food_then_care(client, db):
    kid, common, cat, dog = _setup(db, balance=20)
    login_as(client, "kid")
    page = client.get("/shop").text
    assert "Кошечка" in page and "Корм для питомцев" in page
    r = client.post(f"/shop/pets/{cat.id}/buy", data={"name": "Мурка"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/pets?ok=new"
    assert "Уже у тебя" in client.get("/shop").text
    r = client.post(f"/shop/pets/{dog.id}/buy", data={"name": "Шарик"})
    assert r.status_code == 400 and "Не хватает штампиков" in r.text
    assert client.post("/shop/food", data={"packs": "2"}, follow_redirects=False).status_code == 303
    pet = db.query(Pet).one()
    home = client.get("/").text
    assert "Питомцы ждут заботы" in home and "Мурка: покормить (0 из 2), убрать" in home
    client.post(f"/pets/{pet.id}/feed")
    client.post(f"/pets/{pet.id}/feed")
    assert "уже сыт" in client.post(f"/pets/{pet.id}/feed").text
    client.post(f"/pets/{pet.id}/clean")
    page = client.get("/pets").text
    assert "Сегодня всё сделано!" in page and "Корм: <strong>18</strong>" in page
    assert "Питомцы ждут заботы" not in client.get("/").text


def test_pet_pages_student_only_and_foreign_pet(client, db):
    kid, common, cat, dog = _setup(db)
    pet = pets.buy_pet(db, kid, cat.id, "Мурка")
    make_user(db, login="other")
    login_as(client, "other")
    assert "Питомец не найден" in client.post(f"/pets/{pet.id}/feed").text
    client.post("/logout")
    login_as(client, "teach")
    assert client.get("/pets", follow_redirects=False).status_code == 403


def test_runaway_shown_on_home(client, db):
    from app.timeutil import moscow_today
    kid, common, cat, dog = _setup(db)
    pet = pets.buy_pet(db, kid, cat.id, "Мурка", today=moscow_today() - timedelta(days=5))
    login_as(client, "kid")
    assert "Мурка убежал(а)!" in client.get("/").text
    db.refresh(pet)
    assert pet.ran_away_on == moscow_today()
    assert "убежал(а)" in client.get("/pets").text


def test_admin_pet_settings(client, db):
    kid, common, cat, dog = _setup(db)
    make_user(db, role="admin", login="adm")
    login_as(client, "adm")
    assert "Кошечка" in client.get("/school/pets").text
    data = {"price_type_id": common.id, "food_pack_price": "2", "food_pack_size": "5", "food_max": "40", "runaway_days": "4",
            f"price_{cat.id}": "20", f"active_{cat.id}": "1", f"price_{dog.id}": "25"}
    assert client.post("/school/pets", data=data, follow_redirects=False).status_code == 303
    cfg = db.get(PetConfig, 1)
    db.refresh(cfg)
    db.refresh(cat)
    db.refresh(dog)
    assert (cfg.food_pack_price, cfg.food_pack_size, cfg.food_max, cfg.runaway_days) == (2, 5, 40, 4)
    assert (cat.price, cat.is_active, dog.price, dog.is_active) == (20, True, 25, False)
    assert "вмещать" in client.post("/school/pets", data={**data, "food_max": "3"}).text
    assert "Проверьте числа" in client.post("/school/pets", data={**data, "runaway_days": "0"}).text
