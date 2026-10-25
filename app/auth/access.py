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
