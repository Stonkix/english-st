from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.stamps import StampType
from app.timeutil import utcnow


class PetSpecies(Base):
    """Вид питомца в магазине (кошечка, собачка…). Картинка — app/static/pets/<code>.svg."""

    __tablename__ = "pet_species"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(unique=True)
    name: Mapped[str]
    price: Mapped[int]  # в штампиках вида PetConfig.price_type_id
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)


class PetConfig(Base):
    """Настройки питомцев (одна строка, id = 1): валюта, корм, сколько дней без ухода питомец терпит."""

    __tablename__ = "pet_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    price_type_id: Mapped[int | None] = mapped_column(ForeignKey("stamp_types.id", ondelete="SET NULL"))
    food_pack_price: Mapped[int] = mapped_column(default=1)  # штампиков за пачку корма
    food_pack_size: Mapped[int] = mapped_column(default=10)  # порций в пачке
    food_max: Mapped[int] = mapped_column(default=50)  # больше порций запасти нельзя
    runaway_days: Mapped[int] = mapped_column(default=3)  # столько дней подряд без ухода — и питомец убегает

    price_type: Mapped["StampType | None"] = relationship()


class Pet(Base):
    """Питомец ученика. Живой питомец каждого вида у ученика один; убежавшие остаются в истории."""

    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    species_id: Mapped[int] = mapped_column(ForeignKey("pet_species.id", ondelete="RESTRICT"))
    name: Mapped[str]
    bought_on: Mapped[date]  # день покупки по Москве: с него уход ещё не обязателен
    ran_away_on: Mapped[date | None]  # день по Москве, когда питомец убежал (None — живёт у ученика)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    species: Mapped[PetSpecies] = relationship()


class PetCare(Base):
    """Уход за питомцем за день (по Москве): сколько раз покормили и убрано ли."""

    __tablename__ = "pet_care"
    __table_args__ = (CheckConstraint("feedings >= 0 AND feedings <= 2", name="ck_pet_care_feedings"),)

    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"), primary_key=True)
    day: Mapped[date] = mapped_column(primary_key=True)
    feedings: Mapped[int] = mapped_column(default=0)
    cleaned: Mapped[bool] = mapped_column(default=False)


class PetFood(Base):
    """Запас корма ученика (общий на всех его питомцев), в порциях."""

    __tablename__ = "pet_food"
    __table_args__ = (CheckConstraint("portions >= 0", name="ck_pet_food_portions"),)

    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    portions: Mapped[int] = mapped_column(default=0)
