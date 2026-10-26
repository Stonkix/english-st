"""Питомцы: покупка в магазине, корм, ежедневный уход и побег без ухода.

Уход за день (по Москве): покормить 2 раза и убрать 1 раз. День, когда ухода не хватило, — «без ухода»;
runaway_days таких дней подряд — и питомец убегает (проверяется, когда ученик открывает сайт).
День покупки не считается: купил вечером — ухаживать можно начинать с завтра.
"""

import threading
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.models import Pet, PetCare, PetConfig, PetFood, PetSpecies, StampTransaction, StampType, User
from app.params import MAX_ID
from app.profanity import contains_profanity, load_filter
from app.stamps import BALANCE_LOCK, balance_of
from app.timeutil import moscow_today

NAME_MAX = 20
FEEDINGS_PER_DAY = 2
PACKS_MAX = 50  # за одну покупку

# Уход идёт по одному: два быстрых нажатия «Покормить» не должны съесть две порции и накормить трижды
_CARE_LOCK = threading.Lock()


class PetError(ValueError):
    """Действие не выполнено; текст — для ученика."""


def image_url(species: PetSpecies) -> str:
    return f"/static/pets/{species.code}.svg"


def config(db: DbSession) -> PetConfig:
    """Настройки (одна строка). Если её нет (чистая база), создаётся по умолчанию с валютой «Обычный»."""
    cfg = db.get(PetConfig, 1)
    if cfg is None:
        common = db.scalar(select(StampType).where(StampType.name == "Обычный")) or db.scalar(
            select(StampType).order_by(StampType.sort_order, StampType.id)
        )
        cfg = PetConfig(id=1, price_type_id=common.id if common else None)
        db.add(cfg)
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            cfg = db.get(PetConfig, 1)
    return cfg


def species_list(db: DbSession, active_only: bool = True) -> list[PetSpecies]:
    stmt = select(PetSpecies).order_by(PetSpecies.sort_order, PetSpecies.id)
    if active_only:
        stmt = stmt.where(PetSpecies.is_active.is_(True))
    return list(db.scalars(stmt))


def living_pets(db: DbSession, student_id: int) -> list[Pet]:
    return list(db.scalars(
        select(Pet).where(Pet.student_id == student_id, Pet.ran_away_on.is_(None)).order_by(Pet.id)
    ))


def runaways(db: DbSession, student_id: int, since: date) -> list[Pet]:
    return list(db.scalars(
        select(Pet).where(Pet.student_id == student_id, Pet.ran_away_on >= since).order_by(Pet.id.desc())
    ))


def food(db: DbSession, student_id: int) -> int:
    row = db.get(PetFood, student_id)
    return row.portions if row else 0


def _currency(db: DbSession, cfg: PetConfig) -> StampType:
    currency = db.get(StampType, cfg.price_type_id) if cfg.price_type_id else None
    if currency is None or not currency.is_active:
        raise PetError("Питомцы сейчас недоступны")
    return currency


def _pay(db: DbSession, student: User, currency: StampType, cost: int, reason: str, comment: str) -> None:
    """Списание под BALANCE_LOCK (вызывающий держит замок): не хватает — PetError."""
    have = balance_of(db, student.id, currency.id)
    if have < cost:
        raise PetError(f"Не хватает штампиков: нужно {cost} ({currency.name}), у тебя {have}")
    db.add(StampTransaction(
        student_id=student.id, stamp_type_id=currency.id, amount=-cost, reason=reason, comment=comment,
        actor_id=student.id,
    ))


# ---------- покупки ----------


def buy_pet(db: DbSession, student: User, species_id: int, name: str, today: date | None = None) -> Pet:
    species = db.get(PetSpecies, species_id) if 0 < species_id <= MAX_ID else None
    if species is None or not species.is_active:
        raise PetError("Такого питомца нет в магазине")
    name = " ".join(name.split())
    if not name:
        raise PetError("Придумай питомцу имя")
    if len(name) > NAME_MAX:
        raise PetError(f"Имя — не длиннее {NAME_MAX} символов")
    if contains_profanity(name, load_filter(db)):
        raise PetError("Такое имя не подойдёт. Придумай другое")
    cfg = config(db)
    currency = _currency(db, cfg)
    with BALANCE_LOCK:
        alive = db.scalar(select(Pet.id).where(
            Pet.student_id == student.id, Pet.species_id == species.id, Pet.ran_away_on.is_(None)
        ))
        if alive is not None:
            raise PetError(f"{species.name} у тебя уже есть")
        _pay(db, student, currency, species.price, "pet", f"{species.name} «{name}»")
        pet = Pet(student_id=student.id, species_id=species.id, name=name, bought_on=today or moscow_today())
        db.add(pet)
        db.commit()
    return pet


def buy_food(db: DbSession, student: User, packs: int) -> int:
    """Покупка packs пачек корма; возвращает новый запас в порциях."""
    if not 1 <= packs <= PACKS_MAX:
        raise PetError("Выбери, сколько пачек купить")
    cfg = config(db)
    currency = _currency(db, cfg)
    with BALANCE_LOCK:
        row = db.get(PetFood, student.id)
        have = row.portions if row else 0
        add = packs * cfg.food_pack_size
        if have + add > cfg.food_max:
            raise PetError(f"Столько не поместится: запас — не больше {cfg.food_max} порций, у тебя {have}")
        _pay(db, student, currency, packs * cfg.food_pack_price, "pet_food", f"Корм: {add} порций")
        if row is None:
            db.add(PetFood(student_id=student.id, portions=add))
        else:
            row.portions = have + add
        db.commit()
    return have + add


# ---------- уход ----------


def _care(db: DbSession, pet_id: int, day: date, create: bool = False) -> PetCare | None:
    care = db.get(PetCare, (pet_id, day))
    if care is None and create:
        care = PetCare(pet_id=pet_id, day=day, feedings=0, cleaned=False)
        db.add(care)
    return care


def _own_living(db: DbSession, student: User, pet_id: int) -> Pet:
    pet = db.get(Pet, pet_id) if 0 < pet_id <= MAX_ID else None
    if pet is None or pet.student_id != student.id:
        raise PetError("Питомец не найден")
    if pet.ran_away_on is not None:
        raise PetError(f"{pet.name} убежал(а)")
    return pet


def feed(db: DbSession, student: User, pet_id: int, today: date | None = None) -> PetCare:
    day = today or moscow_today()
    with _CARE_LOCK:
        pet = _own_living(db, student, pet_id)
        care = _care(db, pet.id, day, create=True)
        if care.feedings >= FEEDINGS_PER_DAY:
            raise PetError(f"{pet.name} уже сыт(а) сегодня. Покорми завтра!")
        stock = db.get(PetFood, student.id)
        if stock is None or stock.portions < 1:
            raise PetError("Корм закончился. Купи его в магазине")
        stock.portions -= 1
        care.feedings += 1
        db.commit()
    return care


def clean(db: DbSession, student: User, pet_id: int, today: date | None = None) -> PetCare:
    day = today or moscow_today()
    with _CARE_LOCK:
        pet = _own_living(db, student, pet_id)
        care = _care(db, pet.id, day, create=True)
        if care.cleaned:
            raise PetError("Сегодня уже убрано")
        care.cleaned = True
        db.commit()
    return care


def _cared(care: PetCare | None) -> bool:
    return care is not None and care.feedings >= FEEDINGS_PER_DAY and care.cleaned


def neglected_days(db: DbSession, pet: Pet, today: date, limit: int) -> int:
    """Сколько дней подряд до сегодняшнего (не включая его) питомцу не хватило ухода; считаем не больше limit."""
    count, day = 0, today - timedelta(days=1)
    while count < limit and day > pet.bought_on:
        if _cared(_care(db, pet.id, day)):
            break
        count += 1
        day -= timedelta(days=1)
    return count


def check_runaways(db: DbSession, student_id: int, today: date | None = None) -> list[Pet]:
    """Питомцы, которым runaway_days дней подряд не хватало ухода, убегают сегодня. Возвращает убежавших сейчас."""
    day = today or moscow_today()
    limit = config(db).runaway_days
    gone = []
    for pet in living_pets(db, student_id):
        if neglected_days(db, pet, day, limit) >= limit:
            pet.ran_away_on = day
            gone.append(pet)
    if gone:
        db.commit()
    return gone


@dataclass
class PetStatus:
    pet: Pet
    feedings: int
    cleaned: bool
    neglected: int  # дней подряд без ухода до сегодняшнего
    days_left: int  # сколько ещё дней без ухода питомец потерпит (включая сегодня)

    @property
    def hungry(self) -> bool:
        return self.feedings < FEEDINGS_PER_DAY

    @property
    def done(self) -> bool:
        return not self.hungry and self.cleaned


def statuses(db: DbSession, student_id: int, today: date | None = None) -> list[PetStatus]:
    """Живые питомцы и уход за сегодня. Сначала проверяет, не убежал ли кто."""
    day = today or moscow_today()
    check_runaways(db, student_id, day)
    limit = config(db).runaway_days
    result = []
    for pet in living_pets(db, student_id):
        care = _care(db, pet.id, day)
        neglected = neglected_days(db, pet, day, limit)
        result.append(PetStatus(
            pet=pet, feedings=care.feedings if care else 0, cleaned=bool(care and care.cleaned),
            neglected=neglected, days_left=limit - neglected,
        ))
    return result
