"""Штампики: виды, балансы, журнал, ручная выдача и награды за ежедневное задание (§8.7, §8.2).

Баланс — сумма записей журнала. Всё, что меняет баланс с проверкой «хватает ли», идёт под BALANCE_LOCK:
процесс один (см. englishst.service), так что проверка и запись не разъедутся.
"""

import threading
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Attempt, DailyReward, StampTransaction, StampType, User
from app.params import MAX_ID

# Иконки — Fluent Emoji (Flat), MIT: app/static/stamps
ICONS = {
    "star": "Звезда", "gem": "Кристалл", "crown": "Корона", "heart": "Сердце", "medal": "Медаль",
    "clover": "Клевер", "rainbow": "Радуга", "rocket": "Ракета", "sparkles": "Искры", "apple": "Яблоко",
}
COLORS = {
    "yellow": ("Жёлтый", "#FFF4CC"), "blue": ("Голубой", "#DDF4FF"), "green": ("Зелёный", "#D7FFB8"),
    "red": ("Красный", "#FFDFE0"), "purple": ("Фиолетовый", "#F3E3FF"), "orange": ("Оранжевый", "#FFE7CC"),
}
REASON_TEXT = {
    "manual": "От учителя", "daily": "За задание", "order": "Обмен в магазине", "refund": "Возврат",
    "conversion": "Обмен штампиков",
}
NAME_MAX = 30
AMOUNT_MAX = 1000  # за одну операцию
COMMENT_MAX = 200
REWARD_MAX = 100

BALANCE_LOCK = threading.Lock()


class StampError(ValueError):
    """Операция не выполнена; текст — для пользователя."""


@dataclass(frozen=True)
class Balance:
    stamp_type: StampType
    amount: int


def icon_url(stamp_type: StampType) -> str:
    return f"/static/stamps/{stamp_type.icon}.svg"


def tint(stamp_type: StampType) -> str:
    return COLORS.get(stamp_type.color, COLORS["yellow"])[1]


def all_types(db: DbSession) -> list[StampType]:
    return list(db.scalars(select(StampType).order_by(StampType.sort_order, StampType.id)))


def active_types(db: DbSession) -> list[StampType]:
    return [t for t in all_types(db) if t.is_active]


def balance_of(db: DbSession, student_id: int, stamp_type_id: int) -> int:
    return db.scalar(
        select(func.coalesce(func.sum(StampTransaction.amount), 0)).where(
            StampTransaction.student_id == student_id, StampTransaction.stamp_type_id == stamp_type_id
        )
    )


def balances_by_student(db: DbSession, student_ids: list[int]) -> dict[int, dict[int, int]]:
    """{ученик: {вид: баланс}} одним запросом (для списка группы)."""
    if not student_ids:
        return {}
    rows = db.execute(
        select(StampTransaction.student_id, StampTransaction.stamp_type_id, func.sum(StampTransaction.amount))
        .where(StampTransaction.student_id.in_(student_ids))
        .group_by(StampTransaction.student_id, StampTransaction.stamp_type_id)
    )
    result: dict[int, dict[int, int]] = {sid: {} for sid in student_ids}
    for student_id, type_id, total in rows:
        result[student_id][type_id] = total
    return result


def balances(db: DbSession, student_id: int) -> list[Balance]:
    """Активные виды (даже с нулём) и неактивные, если на них что-то осталось."""
    totals = balances_by_student(db, [student_id])[student_id]
    return [Balance(t, totals.get(t.id, 0)) for t in all_types(db) if t.is_active or totals.get(t.id, 0)]


def history(db: DbSession, student_id: int, limit: int = 50) -> list[StampTransaction]:
    stmt = (
        select(StampTransaction)
        .where(StampTransaction.student_id == student_id)
        .order_by(StampTransaction.id.desc())
        .limit(limit)
    )
    return list(db.scalars(stmt))


def give(db: DbSession, actor: User, student: User, stamp_type_id: int, amount: int, comment: str = "") -> StampTransaction:
    """Ручная выдача (amount > 0) или списание (amount < 0). Списать больше, чем есть, нельзя."""
    stamp_type = db.get(StampType, stamp_type_id) if 0 < stamp_type_id <= MAX_ID else None
    if stamp_type is None or not stamp_type.is_active:
        raise StampError("Выберите вид штампиков")
    if amount == 0 or abs(amount) > AMOUNT_MAX:
        raise StampError(f"Количество — от 1 до {AMOUNT_MAX}")
    comment = " ".join(comment.split())
    if len(comment) > COMMENT_MAX:
        raise StampError(f"Комментарий — не длиннее {COMMENT_MAX} символов")
    with BALANCE_LOCK:
        if amount < 0:
            have = balance_of(db, student.id, stamp_type.id)
            if have + amount < 0:
                raise StampError(f"Столько списать нельзя: у ученика {have}")
        tx = StampTransaction(
            student_id=student.id, stamp_type_id=stamp_type.id, amount=amount, reason="manual",
            comment=comment or None, actor_id=actor.id,
        )
        db.add(tx)
        db.commit()
    return tx


def award_daily(db: DbSession, attempt: Attempt, group_id: int | None) -> None:
    """Награды за завершённую попытку (без commit — его делает завершение попытки).

    Правило срабатывает, если результат не ниже порога, и не чаще раза в день: за день — максимум одна запись.
    """
    if group_id is None or not attempt.total_count:
        return
    percent = attempt.correct_count * 100 / attempt.total_count
    rules = db.scalars(select(DailyReward).where(DailyReward.group_id == group_id).order_by(DailyReward.id))
    for rule in rules:
        if percent < rule.min_score_percent or not rule.stamp_type.is_active:
            continue
        done = db.scalar(
            select(StampTransaction.id).where(
                StampTransaction.student_id == attempt.student_id,
                StampTransaction.reward_id == rule.id,
                StampTransaction.reward_date == attempt.date,
            )
        )
        if done is None:
            db.add(StampTransaction(
                student_id=attempt.student_id, stamp_type_id=rule.stamp_type_id, amount=rule.amount,
                reason="daily", attempt_id=attempt.id, reward_id=rule.id, reward_date=attempt.date,
            ))


def awarded_for(db: DbSession, attempt_id: int) -> list[StampTransaction]:
    return list(db.scalars(
        select(StampTransaction).where(StampTransaction.attempt_id == attempt_id, StampTransaction.reason == "daily")
    ))


def describe(tx: StampTransaction) -> str:
    """Строка истории: «За задание», «От учителя: молодец!»."""
    text = REASON_TEXT.get(tx.reason, "")
    return f"{text}: {tx.comment}" if tx.comment else text
