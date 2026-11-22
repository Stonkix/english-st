from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session as DbSession

from app.auth.deps import require_user
from app.db import get_db
from app.models import StampTransaction, StampType, User
from app.params import MAX_ID
from app.routes.teacher import groups_for_staff, own_group
from app.templating import templates

router = APIRouter()


def _ranked_students(db: DbSession, group_id: int) -> list[dict]:
    stmt = (
        select(User.id, User.first_name, User.last_name, StampType, func.sum(StampTransaction.amount))
        .outerjoin(StampTransaction, and_(
            StampTransaction.student_id == User.id,
            StampTransaction.reason.in_(("manual", "daily")),
        ))
        .outerjoin(StampType, StampType.id == StampTransaction.stamp_type_id)
        .where(User.group_id == group_id, User.role == "student", User.is_active.is_(True))
        .group_by(User.id, User.first_name, User.last_name, StampType.id)
        .order_by(User.last_name, User.first_name, User.id, StampType.sort_order, StampType.id)
    )

    students = {}
    for student_id, first_name, last_name, stamp_type, amount in db.execute(stmt):
        row = students.setdefault(student_id, {
            "student_id": student_id,
            "name": f"{first_name} {last_name}",
            "stamps": [],
            "points": 0,
        })
        if stamp_type is not None:
            row["stamps"].append((stamp_type, amount))
            row["points"] += amount * stamp_type.ranking_points

    rows = sorted(students.values(), key=lambda row: row["points"], reverse=True)
    rank = 0
    previous = None
    for position, row in enumerate(rows, 1):
        score = row["points"]
        if score != previous:
            rank = position
        row["rank"] = rank
        previous = score
    return rows


@router.get("/ranking")
def ranking(
    request: Request,
    group_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    user: User = Depends(require_user),
    db: DbSession = Depends(get_db),
):
    groups = []
    if user.role == "student":
        group = user.group
    else:
        groups = groups_for_staff(db, user)
        group = own_group(db, user, group_id) if group_id is not None else next(iter(groups), None)
    return templates.TemplateResponse(request, "ranking.html", {
        "user": user,
        "groups": groups,
        "group": group,
        "rows": _ranked_students(db, group.id) if group is not None else [],
    })
