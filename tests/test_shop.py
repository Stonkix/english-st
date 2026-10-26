from datetime import timedelta

import pytest

from app import shop, stamps
from app.models import Order, ShopItem, StampTransaction
from app.timeutil import utcnow
from tests.factories import make_group, make_user
from tests.test_stamps import _types


def _setup(db, balance=10, price=4):
    star, gem = _types(db)
    t = make_user(db, role="teacher", login="teach")
    g = make_group(db, t)
    kid = make_user(db, login="kid", group=g)
    item = ShopItem(group_id=g.id, title="Наклейка", price_type_id=star.id, price_amount=price)
    db.add(item)
    db.commit()
    if balance:
        stamps.give(db, t, kid, star.id, balance)
    return t, g, kid, item, star


def test_order_takes_price_snapshot(db):
    t, g, kid, item, star = _setup(db)
    order = shop.place_order(db, kid, item.id)
    assert stamps.balance_of(db, kid.id, star.id) == 6
    item.price_amount = 100
    db.commit()
    db.refresh(order)
    assert order.price_amount == 4 and order.status == "pending"
    tx = db.query(StampTransaction).filter_by(order_id=order.id).one()
    assert (tx.amount, tx.reason) == (-4, "order")


def test_not_enough_stamps(db):
    t, g, kid, item, star = _setup(db, balance=3)
    with pytest.raises(shop.ShopError, match="нужно 4, у тебя 3"):
        shop.place_order(db, kid, item.id)
    assert db.query(Order).count() == 0


def test_foreign_or_hidden_item(db):
    t, g, kid, item, star = _setup(db)
    other = make_group(db, t, name="Другая")
    alien = ShopItem(group_id=other.id, title="Чужое", price_type_id=star.id, price_amount=1)
    db.add(alien)
    item.is_active = False
    db.commit()
    for item_id in (alien.id, item.id, 999999):
        with pytest.raises(shop.ShopError, match="нет в магазине"):
            shop.place_order(db, kid, item_id)


def test_order_rate_limit_counts_cancelled(db):
    t, g, kid, item, star = _setup(db, balance=100, price=1)
    now = utcnow()
    for i in range(3):
        order = shop.place_order(db, kid, item.id, now=now + timedelta(seconds=i))
    shop.cancel_order(db, kid, order.id)
    with pytest.raises(shop.ShopError, match="через 5 мин"):
        shop.place_order(db, kid, item.id, now=now + timedelta(seconds=10))
    with pytest.raises(shop.ShopError, match="через 1 мин"):
        shop.place_order(db, kid, item.id, now=now + timedelta(minutes=4, seconds=30))
    shop.place_order(db, kid, item.id, now=now + timedelta(minutes=5, seconds=1))


def test_cancel_refunds_once(db):
    t, g, kid, item, star = _setup(db)
    order = shop.place_order(db, kid, item.id)
    shop.cancel_order(db, kid, order.id)
    assert stamps.balance_of(db, kid.id, star.id) == 10
    with pytest.raises(shop.ShopError):
        shop.cancel_order(db, kid, order.id)
    other = make_user(db, login="other", group=g)
    order2 = shop.place_order(db, kid, item.id, now=utcnow() + timedelta(seconds=1))
    with pytest.raises(shop.ShopError, match="не найдена"):
        shop.cancel_order(db, other, order2.id)


def test_teacher_issue_and_reject(db):
    t, g, kid, item, star = _setup(db, balance=20)
    a = shop.place_order(db, kid, item.id)
    b = shop.place_order(db, kid, item.id, now=utcnow() + timedelta(seconds=1))
    assert shop.pending_count(db, t) == 2 and [o.id for o in shop.queue(db, t, pending=True)] == [a.id, b.id]
    shop.decide(db, t, a, issue=True)
    shop.decide(db, t, b, issue=False, comment="  нет в наличии ")
    assert (a.status, b.status, b.teacher_comment) == ("issued", "rejected", "нет в наличии")
    assert stamps.balance_of(db, kid.id, star.id) == 16  # 20 - 4 (выдано); отклонённое вернулось
    with pytest.raises(shop.ShopError, match="уже обработана"):
        shop.decide(db, t, b, issue=False)
    with pytest.raises(shop.ShopError):
        shop.cancel_order(db, kid, a.id)  # выданное не отменить
    assert shop.pending_count(db, t) == 0 and len(shop.queue(db, t, pending=False)) == 2


def test_queue_only_own_groups(db):
    t, g, kid, item, star = _setup(db)
    shop.place_order(db, kid, item.id)
    other = make_user(db, role="teacher", login="other")
    admin = make_user(db, role="admin", login="adm")
    order = db.query(Order).one()
    assert shop.pending_count(db, other) == 0 and not shop.can_manage_order(db, other, order)
    assert shop.pending_count(db, admin) == 1 and shop.can_manage_order(db, admin, order)
