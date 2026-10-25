"""Фото профиля: смена, удаление, история прежних фото и запрет смены учителем."""

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.images import InvalidImage, delete_media, read_limited, save_avatar
from app.models import AvatarHistory, User
from app.timeutil import moscow_str, utcnow

BAD_IMAGE = "Не получилось открыть картинку. Попробуйте другое фото."

LOCK_PERIODS: dict[str, timedelta | None] = {
    "1": timedelta(days=1),
    "7": timedelta(days=7),
    "30": timedelta(days=30),
    "forever": None,
}


def read_photo(photo: Any) -> bytes:
    """Читает загруженный файл (не больше лимита) и сразу закрывает временный файл multipart."""
    if photo is None:
        raise InvalidImage("Файл не выбран")
    try:
        return read_limited(photo.file)
    finally:
        photo.file.close()


def avatar_locked(user: User, now: datetime | None = None) -> bool:
    if user.avatar_locked_forever:
        return True
    return user.avatar_locked_until is not None and user.avatar_locked_until > (now or utcnow())


def lock_message(user: User, now: datetime | None = None) -> str | None:
    """Текст для профиля ученика или None, если менять фото можно."""
    if not avatar_locked(user, now):
        return None
    if user.avatar_locked_forever:
        return "Учитель запретил менять фото навсегда"
    return f"Учитель запретил менять фото до {moscow_str(user.avatar_locked_until)}"


def set_avatar_lock(db: DbSession, user: User, period: str | None, now: datetime | None = None) -> None:
    if period is None or period == "off":
        user.avatar_locked_until, user.avatar_locked_forever = None, False
    elif period == "forever":
        user.avatar_locked_until, user.avatar_locked_forever = None, True
    elif period in LOCK_PERIODS:
        user.avatar_locked_until, user.avatar_locked_forever = (now or utcnow()) + LOCK_PERIODS[period], False
    else:
        raise ValueError(f"Неизвестный срок запрета: {period!r}")
    db.commit()


def history(db: DbSession, user: User) -> list[AvatarHistory]:
    """Прежние фото, новые сверху."""
    db.refresh(user, ["avatar_history"])
    return list(user.avatar_history)


def _archive_current(db: DbSession, user: User) -> None:
    if user.avatar:
        db.add(AvatarHistory(user_id=user.id, path=user.avatar))


def replace_avatar(db: DbSession, user: User, data: bytes) -> None:
    """Новое фото; прежнее текущее уходит в историю. Бросает InvalidImage, ничего не меняя."""
    new_path = save_avatar(user.id, data)
    _archive_current(db, user)
    user.avatar = new_path
    try:
        db.commit()
    except Exception:
        db.rollback()
        delete_media(new_path)  # в БД запись не легла — новый файл никому не нужен
        raise


def student_remove_avatar(db: DbSession, user: User) -> None:
    """Ученик убрал фото: оно уходит в историю."""
    _archive_current(db, user)
    user.avatar = None
    db.commit()


def teacher_remove_avatar(db: DbSession, user: User) -> None:
    """Модерация: текущее фото удаляется насовсем (в историю не попадает)."""
    old = user.avatar
    user.avatar = None
    db.commit()
    delete_media(old)


def remove_history_item(db: DbSession, user: User, item_id: int) -> None:
    item = db.get(AvatarHistory, item_id)
    if item is None or item.user_id != user.id:
        raise ValueError("Нет такого фото у этого ученика")
    path = item.path
    db.delete(item)
    db.commit()
    delete_media(path)
