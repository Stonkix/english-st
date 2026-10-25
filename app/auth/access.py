"""Кто чьё фото может видеть."""

from app.models import User


def can_view_avatar(viewer: User, owner: User) -> bool:
    if viewer.id == owner.id or viewer.role == "admin":
        return True
    # учитель видит учеников своих групп
    if owner.group is not None and owner.group.teacher_id == viewer.id:
        return True
    # ученик видит одногруппников и своего учителя
    if viewer.group is not None:
        return owner.group_id == viewer.group_id or owner.id == viewer.group.teacher_id
    return False


def can_view_previous_avatar(viewer: User, owner: User) -> bool:
    """Прежние фото — только админ и учитель группы владельца (не сам ученик и не одногруппники)."""
    if viewer.role == "admin":
        return True
    return owner.group is not None and owner.group.teacher_id == viewer.id


def can_view_question_image(viewer: User, owner_id: int, db) -> bool:
    """Картинки вопроса: автор, админ и ученики групп автора."""
    if viewer.id == owner_id or viewer.role == "admin":
        return True
    return viewer.role == "student" and viewer.group is not None and viewer.group.teacher_id == owner_id
