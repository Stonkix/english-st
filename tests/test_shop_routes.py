from io import BytesIO

from PIL import Image

from app import stamps
from app.config import settings
from app.models import Order, ShopItem
from tests.factories import login_as, make_group, make_user
from tests.test_stamps import _types


def _png() -> bytes:
    buf = BytesIO()
    Image.new("RGB", (60, 40), "green").save(buf, "PNG")
    return buf.getvalue()


def _setup(db, balance=10):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t, name="5А")
    kid = make_user(db, login="kid", group=g)
    if balance:
        stamps.give(db, t, kid, star.id, balance)
    return t, g, kid, star


def test_teacher_creates_item_with_image(client, db):
    t, g, kid, star = _setup(db)
    login_as(client, "teach")
    r = client.post(f"/shop/groups/{g.id}/items",
                    data={"title": " Наклейка ", "description": "блестящая", "price_type_id": star.id, "price_amount": "4"},
                    files={"image": ("a.png", _png(), "image/png")}, follow_redirects=False)
    assert r.status_code == 303
    item = db.query(ShopItem).one()
    assert item.title == "Наклейка" and item.image.startswith(f"shop/{g.id}/")
    assert client.get(f"/media/{item.image}").status_code == 200
    r = client.post(f"/shop/groups/{g.id}/items", data={"title": "", "price_type_id": star.id, "price_amount": "0"})
    assert r.status_code == 400 and "Название" in r.text and "Цена — от 1" in r.text
    r = client.post(f"/shop/groups/{g.id}/items", data={"title": "X", "price_type_id": star.id, "price_amount": "1"},
                    files={"image": ("a.png", b"nope", "image/png")})
    assert "Картинка не подошла" in r.text


def test_item_without_image_and_foreign_group(client, db):
    t, g, kid, star = _setup(db)
    other = make_group(db, make_user(db, role="teacher", login="other"))
    login_as(client, "teach")
    client.post(f"/shop/groups/{g.id}/items", data={"title": "Пятёрка", "price_type_id": star.id, "price_amount": "3"},
                files={"image": ("", b"", "application/octet-stream")})
    assert db.query(ShopItem).one().image is None
    assert client.get(f"/shop/groups/{other.id}").status_code == 404
    assert client.post(f"/shop/groups/{other.id}/items", data={"title": "x", "price_type_id": star.id, "price_amount": "1"}).status_code == 404


def test_student_orders_and_cancels(client, db):
    t, g, kid, star = _setup(db, balance=5)
    cheap = ShopItem(group_id=g.id, title="Наклейка", price_type_id=star.id, price_amount=4)
    pricey = ShopItem(group_id=g.id, title="Книга", price_type_id=star.id, price_amount=50)
    db.add_all([cheap, pricey])
    db.commit()
    login_as(client, "kid")
    page = client.get("/shop").text
    assert "Наклейка" in page and "Не хватает: ещё 45" in page
    r = client.post(f"/shop/items/{cheap.id}/order", follow_redirects=False)
    assert r.status_code == 303
    assert "Заявка отправлена" in client.get("/shop?ok=ordered").text
    assert stamps.balance_of(db, kid.id, star.id) == 1
    r = client.post(f"/shop/items/{pricey.id}/order")
    assert r.status_code == 400 and "Не хватает штампиков" in r.text
    order = db.query(Order).one()
    client.post(f"/shop/orders/{order.id}/cancel")
    assert stamps.balance_of(db, kid.id, star.id) == 5
    assert "Отменено" in client.get("/shop").text


def test_teacher_queue_issue_and_reject(client, db):
    t, g, kid, star = _setup(db, balance=10)
    item = ShopItem(group_id=g.id, title="Наклейка", price_type_id=star.id, price_amount=3)
    db.add(item)
    db.commit()
    login_as(client, "kid")
    client.post(f"/shop/items/{item.id}/order")
    client.post(f"/shop/items/{item.id}/order")
    client.post("/logout")
    login_as(client, "teach")
    page = client.get("/orders").text
    assert page.count("Наклейка") == 2 and 'aria-label="новых заявок: 2"' in page
    first, second = db.query(Order).order_by(Order.id).all()
    client.post(f"/orders/{first.id}/issue")
    client.post(f"/orders/{second.id}/reject", data={"comment": "закончились"})
    db.refresh(first)
    db.refresh(second)
    assert (first.status, second.status, second.teacher_comment) == ("issued", "rejected", "закончились")
    assert stamps.balance_of(db, kid.id, star.id) == 7
    assert "Заявка уже обработана" in client.post(f"/orders/{first.id}/reject").text
    assert client.post(f"/orders/{first.id}/explode").status_code == 404


def test_other_teacher_cannot_decide(client, db):
    t, g, kid, star = _setup(db)
    item = ShopItem(group_id=g.id, title="Наклейка", price_type_id=star.id, price_amount=3)
    db.add(item)
    db.commit()
    from app import shop
    order = shop.place_order(db, kid, item.id)
    make_user(db, role="teacher", login="other")
    login_as(client, "other")
    assert client.post(f"/orders/{order.id}/issue").status_code == 404
    assert "Новых заявок нет" in client.get("/orders").text


def test_edit_toggle_delete(client, db):
    t, g, kid, star = _setup(db)
    login_as(client, "teach")
    client.post(f"/shop/groups/{g.id}/items", data={"title": "Наклейка", "price_type_id": star.id, "price_amount": "3"},
                files={"image": ("a.png", _png(), "image/png")})
    item = db.query(ShopItem).one()
    old_image = item.image
    client.post(f"/shop/items/{item.id}", data={"title": "Стикер", "price_type_id": star.id, "price_amount": "2", "remove_image": "1"})
    db.refresh(item)
    assert (item.title, item.price_amount, item.image) == ("Стикер", 2, None)
    assert not (settings.media_dir / old_image).exists()
    client.post(f"/shop/items/{item.id}/toggle")
    db.refresh(item)
    assert not item.is_active
    client.post("/logout")
    login_as(client, "kid")
    assert "Стикер" not in client.get("/shop").text
    client.post("/logout")
    login_as(client, "teach")
    client.post(f"/shop/items/{item.id}/toggle")
    db.refresh(item)
    from app import shop
    shop.place_order(db, kid, item.id)
    r = client.post(f"/shop/items/{item.id}/delete")
    assert "можно только скрыть" in r.text and db.query(ShopItem).count() == 1


def test_shop_image_access(client, db):
    t, g, kid, star = _setup(db)
    login_as(client, "teach")
    client.post(f"/shop/groups/{g.id}/items", data={"title": "Наклейка", "price_type_id": star.id, "price_amount": "3"},
                files={"image": ("a.png", _png(), "image/png")})
    url = "/media/" + db.query(ShopItem).one().image
    client.post("/logout")
    login_as(client, "kid")
    assert client.get(url).status_code == 200
    client.post("/logout")
    make_user(db, login="stranger", group=make_group(db, make_user(db, role="teacher", login="other")))
    login_as(client, "stranger")
    assert client.get(url).status_code == 404
