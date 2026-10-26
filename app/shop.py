"""Магазин группы: позиции, заявки, очередь учителя, возвраты (§8.8).

Заявка, отмена и отклонение меняют баланс; проверка «хватает ли» и запись идут под stamps.BALANCE_LOCK
одним коммитом: заявка и списание появляются (или нет) вместе.
"""

from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Group, Order, ShopItem, StampTransaction, StampType, User
from app.stamps import BALANCE_LOCK, balance_of
from app.timeutil import utcnow

TITLE_MAX = 60
DESCRIPTION_MAX = 300
PRICE_MAX = 10_000
COMMENT_MAX = 200
ORDER_LIMIT = 3  # заявок
ORDER_WINDOW = timedelta(minutes=5)  # за любые 5 минут, отменённые тоже считаются

STATUS_TEXT = {"pending": "Ждёт учителя", "issued": "Выдано", "rejected": "Отклонено", "cancelled": "Отменено"}


class ShopError(ValueError):
    """Действие не выполнено; текст — для пользователя."""


# ---------- позиции ----------


def item_errors(db: DbSession, title: str, description: str, price_type_id: int, price_amount: int) -> list[str]:
    errors = []
    if not title or len(title) > TITLE_MAX:
        errors.append(f"Название — от 1 до {TITLE_MAX} символов")
    if len(description) > DESCRIPTION_MAX:
        errors.append(f"Описание — не длиннее {DESCRIPTION_MAX} символов")
    price_type = db.get(StampType, price_type_id) if price_type_id > 0 else None
    if price_type is None or not price_type.is_active:
        errors.append("Выберите вид штампиков для цены")
    if not 1 <= price_amount <= PRICE_MAX:
        errors.append(f"Цена — от 1 до {PRICE_MAX}")
    return errors


def group_items(db: DbSession, group_id: int, active_only: bool) -> list[ShopItem]:
    stmt = select(ShopItem).where(ShopItem.group_id == group_id)
    if active_only:
        stmt = stmt.where(ShopItem.is_active.is_(True))
    return list(db.scalars(stmt.order_by(ShopItem.price_amount, ShopItem.title, ShopItem.id)))


def has_orders(db: DbSession, item_id: int) -> bool:
    return db.scalar(select(Order.id).where(Order.item_id == item_id).limit(1)) is not None


# ---------- заявки ----------


def _refund(db: DbSession, order: Order, actor_id: int | None) -> None:
    db.add(StampTransaction(
        student_id=order.student_id, stamp_type_id=order.price_type_id, amount=order.price_amount,
        reason="refund", actor_id=actor_id, order_id=order.id,
    ))


def wait_minutes(db: DbSession, student_id: int, now: datetime | None = None) -> int:
    """Через сколько минут можно подать заявку (0 — можно сейчас)."""
    now = now or utcnow()
    recent = list(db.scalars(
        select(Order.created_at)
        .where(Order.student_id == student_id, Order.created_at > now - ORDER_WINDOW)
        .order_by(Order.created_at)
    ))
    if len(recent) < ORDER_LIMIT:
        return 0
    free_at = recent[len(recent) - ORDER_LIMIT] + ORDER_WINDOW
    return max(1, -(-int((free_at - now).total_seconds()) // 60))


def place_order(db: DbSession, student: User, item_id: int, now: datetime | None = None) -> Order:
    item = db.get(ShopItem, item_id)
    if item is None or not item.is_active or item.group_id != student.group_id:
        raise ShopError("Такой позиции нет в магазине")
    if not item.price_type.is_active:
        raise ShopError("Эту позицию сейчас нельзя обменять")
    with BALANCE_LOCK:
        minutes = wait_minutes(db, student.id, now)
        if minutes:
            raise ShopError(f"Слишком много заявок подряд. Можно снова через {minutes} мин.")
        have = balance_of(db, student.id, item.price_type_id)
        if have < item.price_amount:
            raise ShopError(f"Не хватает штампиков: нужно {item.price_amount}, у тебя {have}")
        order = Order(
            student_id=student.id, item_id=item.id, price_type_id=item.price_type_id,
            price_amount=item.price_amount, created_at=now or utcnow(),
        )
        db.add(order)
        db.flush()  # нужен id заявки для записи журнала
        db.add(StampTransaction(
            student_id=student.id, stamp_type_id=item.price_type_id, amount=-item.price_amount,
            reason="order", order_id=order.id, actor_id=student.id,
        ))
        db.commit()
    return order


def cancel_order(db: DbSession, student: User, order_id: int) -> None:
    """Ученик отменяет свою ждущую заявку — штампики возвращаются."""
    with BALANCE_LOCK:
        order = db.get(Order, order_id)
        if order is None or order.student_id != student.id:
            raise ShopError("Заявка не найдена")
        if order.status != "pending":
            raise ShopError("Учитель уже ответил на эту заявку")
        order.status, order.decided_at, order.decided_by = "cancelled", utcnow(), student.id
        _refund(db, order, student.id)
        db.commit()


def decide(db: DbSession, teacher: User, order: Order, issue: bool, comment: str = "") -> None:
    """Учитель: «Выдано» или «Отклонить» (с возвратом штампиков). Права на заявку проверяет вызывающий."""
    comment = " ".join(comment.split())
    if len(comment) > COMMENT_MAX:
        raise ShopError(f"Комментарий — не длиннее {COMMENT_MAX} символов")
    with BALANCE_LOCK:
        db.refresh(order)
        if order.status != "pending":
            raise ShopError("Заявка уже обработана")
        order.status = "issued" if issue else "rejected"
        order.teacher_comment = comment or None
        order.decided_at, order.decided_by = utcnow(), teacher.id
        if not issue:
            _refund(db, order, teacher.id)
        db.commit()


def student_orders(db: DbSession, student_id: int, limit: int = 30) -> list[Order]:
    return list(db.scalars(
        select(Order).where(Order.student_id == student_id).order_by(Order.id.desc()).limit(limit)
    ))


def _teacher_groups(user: User):
    stmt = select(Group.id)
    return stmt if user.role == "admin" else stmt.where(Group.teacher_id == user.id)


def queue(db: DbSession, user: User, pending: bool, limit: int = 100) -> list[Order]:
    """Заявки учеников групп учителя (админу — всех): ждущие — старые сверху, обработанные — новые сверху."""
    stmt = (
        select(Order).join(ShopItem, ShopItem.id == Order.item_id)
        .where(ShopItem.group_id.in_(_teacher_groups(user)))
    )
    if pending:
        stmt = stmt.where(Order.status == "pending").order_by(Order.id)
    else:
        stmt = stmt.where(Order.status != "pending").order_by(Order.decided_at.desc(), Order.id.desc())
    return list(db.scalars(stmt.limit(limit)))


def pending_count(db: DbSession, user: User) -> int:
    return db.scalar(
        select(func.count()).select_from(Order).join(ShopItem, ShopItem.id == Order.item_id)
        .where(Order.status == "pending", ShopItem.group_id.in_(_teacher_groups(user)))
    )


def can_manage_order(db: DbSession, user: User, order: Order) -> bool:
    if user.role == "admin":
        return True
    group = db.get(Group, order.item.group_id)
    return group is not None and group.teacher_id == user.id
