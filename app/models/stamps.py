from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow

REASONS = ("manual", "penalty", "daily", "order", "refund", "conversion", "pet", "pet_food")


class StampType(Base):
    """Вид штампиков — отдельная валюта со своим балансом. Общие на школу, задаёт админ."""

    __tablename__ = "stamp_types"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(unique=True)
    icon: Mapped[str]  # имя из app.stamps.ICONS (файл app/static/stamps/<icon>.svg)
    color: Mapped[str]  # из app.stamps.COLORS
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    # можно ли получить обменом (легендарный — нельзя: его только выдают)
    craftable: Mapped[bool] = mapped_column(default=True, server_default="1")

    variants: Mapped[list["StampVariant"]] = relationship(
        order_by="(StampVariant.sort_order, StampVariant.id)", cascade="all, delete-orphan", passive_deletes=True,
    )

    def __str__(self) -> str:
        return self.name


class StampVariant(Base):
    """Рисовка штампика. Вид один (баланс общий), а выпадает каждый раз случайная рисовка — их собирают в коллекцию."""

    __tablename__ = "stamp_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    stamp_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(default="")
    icon: Mapped[str | None]  # встроенная картинка (app.stamps.ICONS) …
    image: Mapped[str | None]  # … или загруженная: stamps/<12 hex>.webp относительно media_dir
    sort_order: Mapped[int] = mapped_column(default=0)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class StampTransaction(Base):
    """Журнал: баланс вида = сумма amount. Записи не редактируются и не удаляются."""

    __tablename__ = "stamp_transactions"
    __table_args__ = (
        CheckConstraint("amount <> 0", name="ck_stamp_tx_amount"),
        # правило награды срабатывает не чаще раза в день (NULL в reward_id/reward_date не мешает остальным записям)
        UniqueConstraint("student_id", "reward_id", "reward_date", name="uq_stamp_tx_reward_day"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    stamp_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="RESTRICT"))
    amount: Mapped[int]
    reason: Mapped[str]  # manual | penalty (учитель забрал) | daily | order | refund | conversion | pet | pet_food
    comment: Mapped[str | None]
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    attempt_id: Mapped[int | None] = mapped_column(ForeignKey("attempts.id", ondelete="SET NULL"))
    order_id: Mapped[int | None]  # заявка магазина (этап 5)
    reward_id: Mapped[int | None] = mapped_column(ForeignKey("daily_rewards.id", ondelete="SET NULL"))
    reward_date: Mapped[date | None]  # день по Москве, за который начислена награда
    variant_id: Mapped[int | None] = mapped_column(ForeignKey("stamp_variants.id", ondelete="SET NULL"))  # выпавшая рисовка
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    stamp_type: Mapped[StampType] = relationship()
    variant: Mapped["StampVariant | None"] = relationship()


class DailyReward(Base):
    """Награда за ежедневное задание: amount штампиков вида при результате не ниже min_score_percent.

    Принадлежит группе (любое задание дня) или тесту (только когда в этот день назначен он — тогда вместо наград группы).
    """

    __tablename__ = "daily_rewards"
    __table_args__ = (
        CheckConstraint("(group_id IS NOT NULL) <> (test_id IS NOT NULL)", name="ck_daily_reward_owner"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id", ondelete="CASCADE"), index=True)
    stamp_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="CASCADE"))
    amount: Mapped[int]
    min_score_percent: Mapped[int] = mapped_column(default=0)

    stamp_type: Mapped[StampType] = relationship()


class ConversionRule(Base):
    """Обмен: from_amount штампиков from_type → to_amount штампиков to_type. Правила задаёт админ, меняет ученик."""

    __tablename__ = "conversion_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    from_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="CASCADE"))
    from_amount: Mapped[int]
    to_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="CASCADE"))
    to_amount: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=False)

    from_type: Mapped[StampType] = relationship(foreign_keys=[from_type_id])
    to_type: Mapped[StampType] = relationship(foreign_keys=[to_type_id])
