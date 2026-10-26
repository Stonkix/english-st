"""Магазин: витрина и заявки ученика, позиции группы и очередь заявок учителя (§8.8)."""

import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session as DbSession

from app import shop, stamps
from app.auth.deps import require_role
from app.db import SessionLocal, get_db
from app.images import InvalidImage, delete_media, read_limited, save_shop_image
from app.models import Order, ShopItem, User
from app.params import Id
from app.routes.teacher import own_group
from app.templating import templates

router = APIRouter()
student_only = require_role("student")
teacher_only = require_role("teacher")

BAD_IMAGE = "Картинка не подошла: это не изображение или файл больше 10 МБ"


# ---------- счётчик в меню ----------


def pending_for_nav(user) -> int:
    """Число на вкладке «Заявки». Сбой не должен ломать страницу."""
    if user is None or getattr(user, "id", None) is None or getattr(user, "role", "") == "student":
        return 0
    try:
        with SessionLocal() as db:
            fresh = db.get(User, user.id)
            return shop.pending_count(db, fresh) if fresh is not None else 0
    except Exception:
        logging.exception("Магазин: не удалось посчитать заявки")
        return 0


templates.env.globals["orders_pending"] = pending_for_nav


# ---------- ученик ----------


def _shop_page(request: Request, db: DbSession, user: User, error: str | None = None, ok: str | None = None):
    balances = stamps.balances(db, user.id)
    context = {
        "user": user,
        "items": shop.group_items(db, user.group_id, active_only=True) if user.group_id else [],
        "balances": balances,
        "have": {b.stamp_type.id: b.amount for b in balances},
        "orders": shop.student_orders(db, user.id),
        "status_text": shop.STATUS_TEXT,
        "error": error,
        "ok": ok,
    }
    return templates.TemplateResponse(request, "shop/student.html", context, status_code=400 if error else 200)


OK_MESSAGES = {"ordered": "Заявка отправлена учителю! Штампики списаны.", "cancelled": "Заявка отменена, штампики вернулись."}


@router.get("/shop")
def student_shop(request: Request, ok: str = "", user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    return _shop_page(request, db, user, ok=OK_MESSAGES.get(ok))


@router.post("/shop/items/{item_id}/order")
def order(item_id: Id, request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        shop.place_order(db, user, item_id)
    except shop.ShopError as exc:
        return _shop_page(request, db, user, error=str(exc))
    return RedirectResponse("/shop?ok=ordered", status_code=303)


@router.post("/shop/orders/{order_id}/cancel")
def cancel(order_id: Id, request: Request, user: User = Depends(student_only), db: DbSession = Depends(get_db)):
    try:
        shop.cancel_order(db, user, order_id)
    except shop.ShopError as exc:
        return _shop_page(request, db, user, error=str(exc))
    return RedirectResponse("/shop?ok=cancelled", status_code=303)


# ---------- учитель: позиции группы ----------


def _own_item(db: DbSession, user: User, item_id: int) -> ShopItem:
    item = db.get(ShopItem, item_id)
    if item is None:
        raise HTTPException(404)
    own_group(db, user, item.group_id)  # чужая группа — 404
    return item


def _items_page(request: Request, db: DbSession, user: User, group, **context):
    context.update(
        user=user, group=group, items=shop.group_items(db, group.id, active_only=False),
        stamp_types=stamps.active_types(db),
    )
    context.setdefault("values", {"title": "", "description": "", "price_type_id": "", "price_amount": "5"})
    return templates.TemplateResponse(request, "shop/items.html", context, status_code=400 if context.get("errors") else 200)


def _int(value: str) -> int:
    try:
        return int(value.strip())
    except ValueError:
        return 0


def _read_image(image: UploadFile | None) -> bytes | None:
    """Байты загруженной картинки или None, если файл не выбран. InvalidImage — слишком большой."""
    if image is None or not image.filename:
        return None
    return read_limited(image.file)


@router.get("/shop/groups/{group_id}")
def group_items(group_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _items_page(request, db, user, own_group(db, user, group_id))


@router.post("/shop/groups/{group_id}/items")
def create_item(
    group_id: Id,
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    price_type_id: str = Form(""),
    price_amount: str = Form(""),
    image: UploadFile | None = File(None),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    group = own_group(db, user, group_id)
    title, description = " ".join(title.split()), description.strip()
    values = {"title": title, "description": description, "price_type_id": price_type_id, "price_amount": price_amount}
    errors = shop.item_errors(db, title, description, _int(price_type_id), _int(price_amount))
    rel = None
    if not errors:
        try:
            data = _read_image(image)
            rel = save_shop_image(group.id, data) if data else None
        except InvalidImage:
            errors.append(BAD_IMAGE)
    if errors:
        return _items_page(request, db, user, group, errors=errors, values=values)
    db.add(ShopItem(
        group_id=group.id, title=title, description=description, image=rel,
        price_type_id=_int(price_type_id), price_amount=_int(price_amount),
    ))
    db.commit()
    return RedirectResponse(f"/shop/groups/{group.id}", status_code=303)


def _edit_page(request: Request, db: DbSession, user: User, item: ShopItem, **context):
    context.update(user=user, item=item, stamp_types=stamps.active_types(db), has_orders=shop.has_orders(db, item.id))
    context.setdefault("values", {
        "title": item.title, "description": item.description, "price_type_id": item.price_type_id,
        "price_amount": item.price_amount,
    })
    return templates.TemplateResponse(request, "shop/item_edit.html", context, status_code=400 if context.get("errors") else 200)


@router.get("/shop/items/{item_id}/edit")
def edit_item_form(item_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _edit_page(request, db, user, _own_item(db, user, item_id))


@router.post("/shop/items/{item_id}")
def edit_item(
    item_id: Id,
    request: Request,
    title: str = Form(""),
    description: str = Form(""),
    price_type_id: str = Form(""),
    price_amount: str = Form(""),
    remove_image: str = Form(""),
    image: UploadFile | None = File(None),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    item = _own_item(db, user, item_id)
    title, description = " ".join(title.split()), description.strip()
    values = {"title": title, "description": description, "price_type_id": price_type_id, "price_amount": price_amount}
    errors = shop.item_errors(db, title, description, _int(price_type_id), _int(price_amount))
    new_rel = None
    if not errors:
        try:
            data = _read_image(image)
            new_rel = save_shop_image(item.group_id, data) if data else None
        except InvalidImage:
            errors.append(BAD_IMAGE)
    if errors:
        return _edit_page(request, db, user, item, errors=errors, values=values)
    old = item.image
    if new_rel or remove_image:
        item.image = new_rel
    item.title, item.description = title, description
    item.price_type_id, item.price_amount = _int(price_type_id), _int(price_amount)  # заявки хранят свою цену
    db.commit()
    if old and old != item.image:
        delete_media(old)
    return RedirectResponse(f"/shop/groups/{item.group_id}", status_code=303)


@router.post("/shop/items/{item_id}/toggle")
def toggle_item(item_id: Id, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    """Скрыть позицию из магазина или вернуть. Ждущие заявки на неё остаются в очереди."""
    item = _own_item(db, user, item_id)
    item.is_active = not item.is_active
    db.commit()
    return RedirectResponse(f"/shop/groups/{item.group_id}", status_code=303)


@router.post("/shop/items/{item_id}/delete")
def delete_item(item_id: Id, request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    """Удалить можно только позицию без заявок (иначе пропала бы история) — такую можно скрыть."""
    item = _own_item(db, user, item_id)
    if shop.has_orders(db, item.id):
        return _edit_page(request, db, user, item, errors=["На эту позицию уже были заявки — её можно только скрыть"])
    group_id, image = item.group_id, item.image
    db.delete(item)
    db.commit()
    delete_media(image)
    return RedirectResponse(f"/shop/groups/{group_id}", status_code=303)


# ---------- учитель: заявки ----------


def _orders_page(request: Request, db: DbSession, user: User, error: str | None = None):
    context = {
        "user": user,
        "pending": shop.queue(db, user, pending=True),
        "done": shop.queue(db, user, pending=False, limit=30),
        "status_text": shop.STATUS_TEXT,
        "error": error,
    }
    return templates.TemplateResponse(request, "shop/orders.html", context, status_code=400 if error else 200)


@router.get("/orders")
def orders(request: Request, user: User = Depends(teacher_only), db: DbSession = Depends(get_db)):
    return _orders_page(request, db, user)


@router.post("/orders/{order_id}/{action}")
def decide(
    order_id: Id,
    action: str,
    request: Request,
    comment: str = Form(""),
    user: User = Depends(teacher_only),
    db: DbSession = Depends(get_db),
):
    order = db.get(Order, order_id)
    if action not in ("issue", "reject") or order is None or not shop.can_manage_order(db, user, order):
        raise HTTPException(404)
    try:
        shop.decide(db, user, order, issue=action == "issue", comment=comment)
    except shop.ShopError as exc:
        return _orders_page(request, db, user, error=str(exc))
    return RedirectResponse("/orders", status_code=303)
