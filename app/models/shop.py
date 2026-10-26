from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.stamps import StampType
from app.timeutil import utcnow

ORDER_STATUSES = ("pending", "issued", "rejected", "cancelled")


class ShopItem(Base):
    """Позиция магазина группы. Остатков нет. С заявками не удаляется — только скрывается."""

    __tablename__ = "shop_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), index=True)
    title: Mapped[str]
    description: Mapped[str] = mapped_column(default="")
    image: Mapped[str | None]  # путь относительно media_dir: shop/<group_id>/<12 hex>.webp
    price_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="RESTRICT"))
    price_amount: Mapped[int]
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    price_type: Mapped[StampType] = relationship()


class Order(Base):
    """Заявка ученика. Цена — снимок на момент заявки: учитель может потом поменять цену позиции."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    item_id: Mapped[int] = mapped_column(ForeignKey("shop_items.id", ondelete="RESTRICT"))
    price_type_id: Mapped[int] = mapped_column(ForeignKey("stamp_types.id", ondelete="RESTRICT"))
    price_amount: Mapped[int]
    status: Mapped[str] = mapped_column(default="pending", index=True)  # pending | issued | rejected | cancelled
    teacher_comment: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow, index=True)
    decided_at: Mapped[datetime | None]
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))

    item: Mapped[ShopItem] = relationship()
    price_type: Mapped[StampType] = relationship()
    student: Mapped["User"] = relationship(foreign_keys=[student_id])  # noqa: F821
