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
    points = func.coalesce(func.sum(StampTransaction.amount * StampType.ranking_points), 0).label("points")
    stmt = (
        select(User.id, User.first_name, User.last_name, points)
        .outerjoin(StampTransaction, and_(
            StampTransaction.student_id == User.id,
            StampTransaction.reason.in_(("manual", "daily")),
        ))
        .outerjoin(StampType, StampType.id == StampTransaction.stamp_type_id)
        .where(User.group_id == group_id, User.role == "student", User.is_active.is_(True))
        .group_by(User.id, User.first_name, User.last_name)
        .order_by(points.desc(), User.last_name, User.first_name, User.id)
    )
    rows = []
    rank = 0
    previous = None
    for position, (student_id, first_name, last_name, score) in enumerate(db.execute(stmt), 1):
        if score != previous:
            rank = position
        rows.append({"rank": rank, "student_id": student_id, "name": f"{first_name} {last_name}", "points": score})
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
