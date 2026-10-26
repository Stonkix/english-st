"""Штампики: виды, балансы, журнал, ручная выдача и награды за ежедневное задание (§8.7, §8.2).

Баланс — сумма записей журнала. Всё, что меняет баланс с проверкой «хватает ли», идёт под BALANCE_LOCK:
процесс один (см. englishst.service), так что проверка и запись не разъедутся.
"""

import random
import threading
from datetime import timedelta
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Attempt, ConversionRule, DailyReward, StampTransaction, StampType, StampVariant, User
from app.params import MAX_ID
from app.timeutil import utcnow

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
    "manual": "От учителя", "penalty": "Учитель забрал", "daily": "За задание", "order": "Обмен в магазине", "refund": "Возврат",
    "conversion": "Обмен",
}
# подсказки причины в форме «Забрать штампики» (учитель может написать свою)
PENALTY_HINTS = ("За поведение на уроке", "За грубость", "За опоздание", "За невыполненное домашнее задание", "За списывание")
NAME_MAX = 30
AMOUNT_MAX = 1000  # за одну операцию
RULE_AMOUNT_MAX = 1000
EXCHANGE_TIMES_MAX = 100  # обменов за одно нажатие
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
    """Ручная выдача: amount > 0 штампиков активного вида, комментарий необязателен."""
    stamp_type = db.get(StampType, stamp_type_id) if 0 < stamp_type_id <= MAX_ID else None
    if stamp_type is None or not stamp_type.is_active:
        raise StampError("Выберите вид штампиков")
    if not 1 <= amount <= AMOUNT_MAX:
        raise StampError(f"Количество — от 1 до {AMOUNT_MAX}")
    comment = _clean_comment(comment)
    with BALANCE_LOCK:
        tx = StampTransaction(
            student_id=student.id, stamp_type_id=stamp_type.id, amount=amount, reason="manual",
            comment=comment or None, actor_id=actor.id, variant_id=pick_variant(db, stamp_type.id),
        )
        db.add(tx)
        db.commit()
    return tx


def take(db: DbSession, actor: User, student: User, stamp_type_id: int, amount: int, reason: str) -> StampTransaction:
    """Учитель забирает штампики любого вида (в том числе выключенного) с обязательной причиной — ученик её увидит.

    Забрать больше, чем есть, нельзя.
    """
    stamp_type = db.get(StampType, stamp_type_id) if 0 < stamp_type_id <= MAX_ID else None
    if stamp_type is None:
        raise StampError("Выберите вид штампиков")
    if not 1 <= amount <= AMOUNT_MAX:
        raise StampError(f"Количество — от 1 до {AMOUNT_MAX}")
    reason = _clean_comment(reason)
    if not reason:
        raise StampError("Напишите, за что забираете штампики — ученик это увидит")
    with BALANCE_LOCK:
        have = balance_of(db, student.id, stamp_type.id)
        if have < amount:
            raise StampError(f"Столько забрать нельзя: у ученика {have} ({stamp_type.name})")
        tx = StampTransaction(
            student_id=student.id, stamp_type_id=stamp_type.id, amount=-amount, reason="penalty",
            comment=reason, actor_id=actor.id,
        )
        db.add(tx)
        db.commit()
    return tx


def _clean_comment(text: str) -> str:
    text = " ".join(text.split())
    if len(text) > COMMENT_MAX:
        raise StampError(f"Комментарий — не длиннее {COMMENT_MAX} символов")
    return text


def recent_penalties(db: DbSession, student_id: int, days: int = 3) -> list[StampTransaction]:
    """Что учитель забрал за последние дни — показывается ученику на главной."""
    since = utcnow() - timedelta(days=days)
    return list(db.scalars(
        select(StampTransaction)
        .where(StampTransaction.student_id == student_id, StampTransaction.reason == "penalty",
               StampTransaction.created_at >= since)
        .order_by(StampTransaction.id.desc())
    ))


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
                variant_id=pick_variant(db, rule.stamp_type_id),
            ))


def awarded_for(db: DbSession, attempt_id: int) -> list[StampTransaction]:
    return list(db.scalars(
        select(StampTransaction).where(StampTransaction.attempt_id == attempt_id, StampTransaction.reason == "daily")
    ))


def describe(tx: StampTransaction) -> str:
    """Строка истории: «За задание», «От учителя: молодец!»."""
    text = REASON_TEXT.get(tx.reason, "")
    return f"{text}: {tx.comment}" if tx.comment else text


# ---------- рисовки и коллекция ----------


def pick_variant(db: DbSession, stamp_type_id: int, rng: random.Random | None = None) -> int | None:
    """Случайная рисовка вида для нового штампика (None — рисовок нет, тогда показывается картинка вида)."""
    ids = list(db.scalars(
        select(StampVariant.id).where(StampVariant.stamp_type_id == stamp_type_id, StampVariant.is_active.is_(True))
    ))
    return (rng or random).choice(ids) if ids else None


def variant_url(variant: StampVariant) -> str:
    return f"/media/{variant.image}" if variant.image else f"/static/stamps/{variant.icon or 'star'}.svg"


def tx_picture(tx: StampTransaction) -> str:
    """Картинка строки истории: выпавшая рисовка или картинка вида."""
    return variant_url(tx.variant) if tx.variant is not None else icon_url(tx.stamp_type)


@dataclass
class CollectionRow:
    stamp_type: StampType
    variants: list[tuple[StampVariant, bool]] = field(default_factory=list)  # (рисовка, есть ли у ученика)

    @property
    def owned(self) -> int:
        return sum(1 for _, has in self.variants if has)


def collection(db: DbSession, student_id: int) -> list[CollectionRow]:
    """Какие рисовки ученику уже выпадали. Скрытые админом показываются, только если уже выпали."""
    got = set(db.scalars(
        select(StampTransaction.variant_id).where(
            StampTransaction.student_id == student_id, StampTransaction.variant_id.is_not(None)
        ).distinct()
    ))
    rows = []
    for t in all_types(db):
        variants = [(v, v.id in got) for v in t.variants if v.is_active or v.id in got]
        if t.is_active and variants:
            rows.append(CollectionRow(t, variants))
    return rows


# ---------- обмен ----------


def rule_text(rule: ConversionRule) -> str:
    return f"{rule.from_amount} {rule.from_type.name} → {rule.to_amount} {rule.to_type.name}"


def rule_errors(db: DbSession, from_type_id: int, from_amount: int, to_type_id: int, to_amount: int) -> list[str]:
    errors = []
    src = db.get(StampType, from_type_id) if 0 < from_type_id <= MAX_ID else None
    dst = db.get(StampType, to_type_id) if 0 < to_type_id <= MAX_ID else None
    if src is None or dst is None:
        errors.append("Выберите оба вида")
    elif src.id == dst.id:
        errors.append("Обменивать вид сам на себя нельзя")
    elif not dst.craftable:
        errors.append(f"«{dst.name}» нельзя получить обменом — только выдать")
    if not 1 <= from_amount <= RULE_AMOUNT_MAX or not 1 <= to_amount <= RULE_AMOUNT_MAX:
        errors.append(f"Количество — от 1 до {RULE_AMOUNT_MAX}")
    return errors


def _usable(rule: ConversionRule) -> bool:
    return rule.is_active and rule.from_type.is_active and rule.to_type.is_active and rule.to_type.craftable


def all_rules(db: DbSession) -> list[ConversionRule]:
    return list(db.scalars(select(ConversionRule).order_by(ConversionRule.id)))


def exchange_rules(db: DbSession) -> list[ConversionRule]:
    """Правила, доступные ученику сейчас."""
    return [r for r in all_rules(db) if _usable(r)]


def exchange(db: DbSession, student: User, rule_id: int, times: int = 1) -> StampTransaction:
    """Обмен по правилу times раз: списание и начисление одной транзакцией БД. Возвращает начисление."""
    rule = db.get(ConversionRule, rule_id) if 0 < rule_id <= MAX_ID else None
    if rule is None or not _usable(rule):
        raise StampError("Такого обмена нет")
    if not 1 <= times <= EXCHANGE_TIMES_MAX:
        raise StampError(f"За раз — от 1 до {EXCHANGE_TIMES_MAX} обменов")
    cost, gain = rule.from_amount * times, rule.to_amount * times
    with BALANCE_LOCK:
        have = balance_of(db, student.id, rule.from_type_id)
        if have < cost:
            raise StampError(f"Не хватает: нужно {cost} ({rule.from_type.name}), у тебя {have}")
        text = rule_text(rule) + (f" × {times}" if times > 1 else "")
        db.add(StampTransaction(
            student_id=student.id, stamp_type_id=rule.from_type_id, amount=-cost, reason="conversion",
            comment=text, actor_id=student.id,
        ))
        got = StampTransaction(
            student_id=student.id, stamp_type_id=rule.to_type_id, amount=gain, reason="conversion",
            comment=text, actor_id=student.id, variant_id=pick_variant(db, rule.to_type_id),
        )
        db.add(got)
        db.commit()
    return got
