from datetime import datetime

from sqlalchemy import ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.timeutil import utcnow

ROLES = ("student", "teacher", "admin")


class User(Base):
    __tablename__ = "users"

    ROLES = ROLES

    id: Mapped[int] = mapped_column(primary_key=True)
    login: Mapped[str] = mapped_column(unique=True, index=True)
    password_hash: Mapped[str]
    role: Mapped[str]  # student | teacher | admin
    first_name: Mapped[str]
    last_name: Mapped[str]
    avatar: Mapped[str | None]  # путь относительно media_dir
    group_id: Mapped[int | None] = mapped_column(
        ForeignKey("groups.id", ondelete="RESTRICT", use_alter=True, name="fk_users_group_id")
    )  # use_alter: users и groups ссылаются друг на друга
    avatar_locked_until: Mapped[datetime | None]  # учитель запретил ученику менять фото до этого момента (UTC)
    avatar_locked_forever: Mapped[bool] = mapped_column(default=False)
    must_change_password: Mapped[bool] = mapped_column(default=False)
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    group: Mapped["Group | None"] = relationship(back_populates="students", foreign_keys=[group_id])

    avatar_history: Mapped[list["AvatarHistory"]] = relationship(
        back_populates="user",
        order_by="(AvatarHistory.created_at.desc(), AvatarHistory.id.desc())",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}"

    def __str__(self) -> str:  # подпись в списках админки
        return f"{self.full_name} ({self.login})"


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str]
    teacher_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    students: Mapped[list[User]] = relationship(back_populates="group", foreign_keys=[User.group_id])
    teacher: Mapped[User] = relationship(foreign_keys=[teacher_id])

    def __str__(self) -> str:
        return self.name


class AvatarHistory(Base):
    """Прежнее фото ученика: видно только учителю группы и админу. Хранятся все."""

    __tablename__ = "avatar_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path: Mapped[str]  # путь относительно media_dir
    created_at: Mapped[datetime] = mapped_column(default=utcnow)  # когда фото стало «предыдущим»

    user: Mapped[User] = relationship(back_populates="avatar_history")
