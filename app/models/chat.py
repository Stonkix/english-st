from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.accounts import User
from app.timeutil import utcnow

ROOM_KINDS = ("group", "direct")
MESSAGE_KINDS = ("text", "image", "sticker")


class ChatRoom(Base):
    """Комната: чат группы (group_id) или личный «ученик ↔ учитель его группы» (student_id). Создаются сами."""

    __tablename__ = "chat_rooms"
    __table_args__ = (
        CheckConstraint(
            "(kind = 'group' AND group_id IS NOT NULL AND student_id IS NULL)"
            " OR (kind = 'direct' AND student_id IS NOT NULL AND group_id IS NULL)",
            name="ck_chat_room_target",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str]  # group | direct
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id", ondelete="CASCADE"), unique=True)
    # собеседник личной комнаты — учитель текущей группы ученика (после перевода — новый учитель)
    student_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_room_id_id", "room_id", "id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("chat_rooms.id", ondelete="CASCADE"))
    author_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str]  # text | image | sticker
    text_original: Mapped[str | None]  # как написал автор (видят учитель комнаты и админ)
    text_shown: Mapped[str | None]  # после фильтра мата
    was_filtered: Mapped[bool] = mapped_column(default=False)
    sticker: Mapped[str | None]  # имя из app.chat.STICKERS
    image: Mapped[str | None]  # путь относительно media_dir: chat/<room_id>/<12 hex>.webp
    deleted_at: Mapped[datetime | None]
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    author: Mapped[User] = relationship(foreign_keys=[author_id])


class ChatReadMark(Base):
    """До какого сообщения пользователь дочитал комнату."""

    __tablename__ = "chat_read_marks"

    room_id: Mapped[int] = mapped_column(ForeignKey("chat_rooms.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    last_read_message_id: Mapped[int] = mapped_column(default=0)


class BadWord(Base):
    """Корень бранного слова. «^» в начале — только в начале слова (иначе — в любом месте)."""

    __tablename__ = "bad_words"

    id: Mapped[int] = mapped_column(primary_key=True)
    pattern: Mapped[str] = mapped_column(unique=True)
    lang: Mapped[str]  # ru | en


class BadWordException(Base):
    """Обычное слово, внутри которого есть «плохой» корень (рубля, застрахуй): такое совпадение не считается."""

    __tablename__ = "bad_word_exceptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    word: Mapped[str] = mapped_column(unique=True)


class ReplacementWord(Base):
    """Безобидное слово, которым заменяется бранное."""

    __tablename__ = "replacement_words"

    id: Mapped[int] = mapped_column(primary_key=True)
    word: Mapped[str] = mapped_column(unique=True)
