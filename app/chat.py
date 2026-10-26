"""Чат: комнаты, права, сообщения, история, непрочитанные, лимит частоты (§8.6). Без HTTP — его делают маршруты.

Комнаты создаются сами: одна на группу и одна на ученика («ученик ↔ учитель его группы»).
Участники считаются по текущему составу: исключённый из группы или отключённый ученик сразу теряет доступ.
"""

import re
import threading
import time
from collections import deque
from datetime import timezone

from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.images import delete_media
from app.models import ChatMessage, ChatReadMark, ChatRoom, Group, User
from app.profanity import clean_text, load_filter
from app.templating import avatar_url
from app.timeutil import MOSCOW, utcnow

TEXT_MAX = 1000
PAGE_SIZE = 50
# Fluent Emoji (Flat), MIT: app/static/stickers. Имя — файл без .svg, подпись — для экранного диктора
STICKERS = {
    "thumbs_up": "Класс", "heart": "Сердце", "smile": "Улыбка", "laugh": "Смешно", "star": "Восторг",
    "party": "Ура", "clap": "Аплодисменты", "fire": "Огонь", "hundred": "Сто баллов", "trophy": "Кубок",
    "ok": "Готово", "books": "Учёба", "rocket": "Ракета", "thinking": "Хм", "wave": "Привет", "cat": "Котик",
    "sun": "Солнце", "sad": "Грустно",
}

EMPTY_TEXT = "Напиши сообщение"
TOO_LONG = f"Сообщение длиннее {TEXT_MAX} символов"
TOO_FAST = "Слишком много сообщений. Подожди немного."
NO_ACCESS = "Нет доступа к этому чату"

_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_MANY_NEWLINES = re.compile(r"\n{3,}")


class ChatError(Exception):
    """Сообщение не принято; текст — для ученика."""


# ---------- лимит частоты ----------


class RateLimiter:
    """Не чаще 1 сообщения в секунду и 20 в минуту на пользователя. Память процесса (он один)."""

    def __init__(self, per_second: int = 1, per_minute: int = 20):
        self.per_second, self.per_minute = per_second, per_minute
        self._hits: dict[int, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, user_id: int, now: float | None = None) -> bool:
        """Засчитывает сообщение; False — лимит превышен (тогда не засчитывается)."""
        now = time.monotonic() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(user_id, deque())
            while hits and now - hits[0] >= 60:
                hits.popleft()
            last_second = sum(1 for t in hits if now - t < 1)
            if last_second >= self.per_second or len(hits) >= self.per_minute:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def check_rate(user: User) -> None:
    if not limiter.hit(user.id):
        raise ChatError(TOO_FAST)


# ---------- комнаты и права ----------


def _get_or_create(db: DbSession, **target) -> ChatRoom:
    stmt = select(ChatRoom).filter_by(**target)
    room = db.scalar(stmt)
    if room is not None:
        return room
    kind = "group" if "group_id" in target else "direct"
    db.add(ChatRoom(kind=kind, **target))
    try:
        db.commit()
    except IntegrityError:  # создали параллельным запросом
        db.rollback()
    return db.scalars(stmt).one()


def group_room(db: DbSession, group_id: int) -> ChatRoom:
    return _get_or_create(db, group_id=group_id)


def direct_room(db: DbSession, student_id: int) -> ChatRoom:
    return _get_or_create(db, student_id=student_id)


def room_teacher_id(db: DbSession, room: ChatRoom) -> int | None:
    """Учитель комнаты: учитель группы (для личной — текущей группы ученика)."""
    group_id = room.group_id
    if room.kind == "direct":
        student = db.get(User, room.student_id)
        group_id = student.group_id if student is not None else None
    group = db.get(Group, group_id) if group_id is not None else None
    return group.teacher_id if group is not None else None


def can_access(db: DbSession, user: User, room: ChatRoom) -> bool:
    if user.role == "admin":
        return True
    if user.role == "student":
        if user.group_id is None:
            return False
        return room.group_id == user.group_id if room.kind == "group" else room.student_id == user.id
    return room_teacher_id(db, room) == user.id


def can_moderate(db: DbSession, user: User, room: ChatRoom) -> bool:
    """Удалять любые сообщения и видеть исходный текст: учитель комнаты и админ."""
    return user.role == "admin" or (user.role == "teacher" and room_teacher_id(db, room) == user.id)


def participants(db: DbSession, room: ChatRoom) -> dict[int, bool]:
    """Кому рассылать сообщения комнаты: id пользователя → модератор ли он."""
    result: dict[int, bool] = {}
    if room.kind == "group":
        students = db.scalars(select(User.id).where(User.group_id == room.group_id, User.is_active.is_(True)))
        result.update(dict.fromkeys(students, False))
    else:
        student = db.get(User, room.student_id)
        if student is not None and student.is_active and student.group_id is not None:
            result[student.id] = False
    teacher_id = room_teacher_id(db, room)
    if teacher_id is not None:
        result[teacher_id] = True
    admins = db.scalars(select(User.id).where(User.role == "admin", User.is_active.is_(True)))
    result.update(dict.fromkeys(admins, True))
    return result


def rooms_for(db: DbSession, user: User) -> list[ChatRoom]:
    """Комнаты пользователя (создаются при первом обращении): ученику — группа и учитель, учителю — все свои."""
    if user.role == "student":
        if user.group_id is None:
            return []
        return [group_room(db, user.group_id), direct_room(db, user.id)]
    stmt = select(Group).order_by(Group.name, Group.id)
    if user.role != "admin":
        stmt = stmt.where(Group.teacher_id == user.id)
    rooms = []
    for group in db.scalars(stmt).all():
        rooms.append(group_room(db, group.id))
        students = sorted(
            (s for s in group.students if s.is_active), key=lambda s: (s.last_name, s.first_name, s.id)
        )
        rooms.extend(direct_room(db, s.id) for s in students)
    return rooms


def room_title(db: DbSession, room: ChatRoom, viewer: User) -> str:
    if room.kind == "group":
        return db.get(Group, room.group_id).name
    if viewer.role == "student":
        teacher_id = room_teacher_id(db, room)
        teacher = db.get(User, teacher_id) if teacher_id else None
        return teacher.full_name if teacher else "Учитель"
    return db.get(User, room.student_id).full_name


def room_avatar_user(db: DbSession, room: ChatRoom, viewer: User) -> User | None:
    """Чьё фото показать у личной комнаты (у комнаты группы фото нет)."""
    if room.kind == "group":
        return None
    if viewer.role == "student":
        teacher_id = room_teacher_id(db, room)
        return db.get(User, teacher_id) if teacher_id else None
    return db.get(User, room.student_id)


# ---------- сообщения ----------


def _clean_input(text: str) -> str:
    text = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", " "))
    return _MANY_NEWLINES.sub("\n\n", text).strip()


def post_text(db: DbSession, user: User, room: ChatRoom, text: str) -> ChatMessage:
    text = _clean_input(text)
    if not text:
        raise ChatError(EMPTY_TEXT)
    if len(text) > TEXT_MAX:
        raise ChatError(TOO_LONG)
    check_rate(user)
    shown, filtered = clean_text(text, load_filter(db))
    return _save(db, ChatMessage(
        room_id=room.id, author_id=user.id, kind="text",
        text_original=text, text_shown=shown, was_filtered=filtered,
    ))


def post_sticker(db: DbSession, user: User, room: ChatRoom, sticker: str) -> ChatMessage:
    if sticker not in STICKERS:
        raise ChatError("Такого стикера нет")
    check_rate(user)
    return _save(db, ChatMessage(room_id=room.id, author_id=user.id, kind="sticker", sticker=sticker))


def post_image(db: DbSession, user: User, room: ChatRoom, rel: str) -> ChatMessage:
    """rel — уже сохранённая картинка (images.save_chat_image); лимит частоты проверяют до её обработки."""
    return _save(db, ChatMessage(room_id=room.id, author_id=user.id, kind="image", image=rel))


def _save(db: DbSession, message: ChatMessage) -> ChatMessage:
    db.add(message)
    db.commit()
    db.refresh(message)
    mark_read(db, message.author_id, message.room_id, message.id)  # своё сообщение прочитано
    return message


def can_delete(user: User, message: ChatMessage, moderator: bool) -> bool:
    return message.deleted_at is None and (moderator or message.author_id == user.id)


def delete_message(db: DbSession, user: User, message: ChatMessage, moderator: bool) -> bool:
    """Удаляет у всех; файл картинки стирается с диска, исходный текст остаётся в БД для модерации."""
    if not can_delete(user, message, moderator):
        return False
    message.deleted_at, message.deleted_by = utcnow(), user.id
    image, message.image = message.image, None
    db.commit()
    delete_media(image)
    return True


def history(db: DbSession, room: ChatRoom, before: int | None = None, after: int | None = None) -> list[ChatMessage]:
    """По возрастанию id: последние PAGE_SIZE (до before) или первые PAGE_SIZE после after (догрузка после обрыва)."""
    stmt = select(ChatMessage).where(ChatMessage.room_id == room.id)
    if after is not None:
        rows = db.scalars(stmt.where(ChatMessage.id > after).order_by(ChatMessage.id).limit(PAGE_SIZE)).all()
        return list(rows)
    if before is not None:
        stmt = stmt.where(ChatMessage.id < before)
    rows = db.scalars(stmt.order_by(ChatMessage.id.desc()).limit(PAGE_SIZE)).all()
    return list(reversed(rows))


def message_url(rel: str) -> str:
    return f"/media/{rel}"


def serialize(message: ChatMessage, moderator: bool) -> dict:
    """Сообщение для браузера. Исходный текст отфильтрованного — только модератору."""
    author = message.author
    local = message.created_at.replace(tzinfo=timezone.utc).astimezone(MOSCOW)
    data = {
        "id": message.id,
        "room_id": message.room_id,
        "author": {
            "id": author.id,
            "name": author.full_name,
            "initials": (author.first_name[:1] + author.last_name[:1]).upper(),
            "avatar": avatar_url(author),
            "teacher": author.role != "student",
        },
        "kind": message.kind,
        "time": local.strftime("%H:%M"),
        "day": local.date().isoformat(),
        "deleted": message.deleted_at is not None,
    }
    if data["deleted"]:
        return data
    if message.kind == "text":
        data["text"] = message.text_shown
        if moderator and message.was_filtered:
            data["original"] = message.text_original
    elif message.kind == "sticker":
        data["sticker"] = f"/static/stickers/{message.sticker}.svg"
        data["label"] = STICKERS.get(message.sticker, "Стикер")
    elif message.kind == "image" and message.image:
        data["image"] = message_url(message.image)
    return data


def preview(message: ChatMessage | None, viewer: User) -> str:
    """Строка под названием комнаты в списке."""
    if message is None:
        return "Сообщений пока нет"
    if message.deleted_at is not None:
        body = "Сообщение удалено"
    elif message.kind == "sticker":
        body = "Стикер"
    elif message.kind == "image":
        body = "Фото"
    else:
        body = message.text_shown or ""
    return ("Вы: " if message.author_id == viewer.id else "") + body


def last_messages(db: DbSession, room_ids: list[int]) -> dict[int, ChatMessage]:
    if not room_ids:
        return {}
    last_ids = select(func.max(ChatMessage.id)).where(ChatMessage.room_id.in_(room_ids)).group_by(ChatMessage.room_id)
    return {m.room_id: m for m in db.scalars(select(ChatMessage).where(ChatMessage.id.in_(last_ids)))}


# ---------- непрочитанные ----------


def mark_read(db: DbSession, user_id: int, room_id: int, message_id: int) -> None:
    """Отметка только растёт: старое «прочитано» не откатывает новое."""
    mark = db.get(ChatReadMark, (room_id, user_id))
    if mark is None:
        db.add(ChatReadMark(room_id=room_id, user_id=user_id, last_read_message_id=message_id))
    elif message_id > mark.last_read_message_id:
        mark.last_read_message_id = message_id
    else:
        return
    try:
        db.commit()
    except IntegrityError:  # отметку создал параллельный запрос
        db.rollback()
        mark_read(db, user_id, room_id, message_id)


def unread_counts(db: DbSession, user: User, room_ids: list[int]) -> dict[int, int]:
    """Непрочитанные чужие неудалённые сообщения по комнатам."""
    if not room_ids:
        return {}
    mark = ChatReadMark
    stmt = (
        select(ChatMessage.room_id, func.count())
        .outerjoin(mark, and_(mark.room_id == ChatMessage.room_id, mark.user_id == user.id))
        .where(
            ChatMessage.room_id.in_(room_ids),
            ChatMessage.author_id != user.id,
            ChatMessage.deleted_at.is_(None),
            ChatMessage.id > func.coalesce(mark.last_read_message_id, 0),
        )
        .group_by(ChatMessage.room_id)
    )
    return dict(db.execute(stmt).all())


def unread_total(db: DbSession, user: User) -> int:
    return sum(unread_counts(db, user, [r.id for r in rooms_for(db, user)]).values())
