"""Сессии в БД: в cookie лежит случайный токен, в таблице — только его sha256.

Срок жизни скользящий: 30 дней с последнего «продления», а продлеваем не чаще
раза в час (TOUCH_EVERY), чтобы не писать в БД на каждый запрос.
"""

import hashlib
import secrets
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.config import settings
from app.models import Session, User
from app.timeutil import utcnow

COOKIE_NAME = "est_session"
SESSION_TTL = timedelta(days=30)
TOUCH_EVERY = timedelta(hours=1)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_session(db: DbSession, user: User, ip: str, user_agent: str, now: datetime | None = None) -> str:
    """Создаёт сессию и возвращает сырой токен (в БД его нет)."""
    now = now or utcnow()
    token = secrets.token_urlsafe(32)
    db.add(
        Session(
            token_hash=_hash(token),
            user_id=user.id,
            created_at=now,
            last_seen_at=now,
            expires_at=now + SESSION_TTL,
            ip=ip,
            user_agent=user_agent,
        )
    )
    db.commit()
    return token


def resolve_session(db: DbSession, token: str | None, now: datetime | None = None) -> tuple[User | None, bool]:
    """(пользователь, продлена ли сессия). Продление не чаще TOUCH_EVERY.

    Если сессия продлена, вызывающий должен заново выдать cookie (set_session_cookie),
    иначе браузер забудет её по исходному max_age, а на сервере сессия останется.
    """
    if not token:
        return None, False
    now = now or utcnow()
    row = db.get(Session, _hash(token))
    if row is None:
        return None, False
    if row.expires_at <= now:
        db.delete(row)
        db.commit()
        return None, False
    user = db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None, False
    touched = now - row.last_seen_at >= TOUCH_EVERY
    if touched:
        row.last_seen_at = now
        row.expires_at = now + SESSION_TTL
        db.commit()
    return user, touched


def get_session_user(db: DbSession, token: str | None, now: datetime | None = None) -> User | None:
    """Пользователь по токену или None: нет токена, сессия истекла, аккаунт отключён."""
    return resolve_session(db, token, now)[0]


def revoke_session(db: DbSession, token: str) -> None:
    db.execute(delete(Session).where(Session.token_hash == _hash(token)))
    db.commit()


def revoke_user_sessions(db: DbSession, user_id: int, keep_token: str | None = None) -> int:
    """Завершает все сессии пользователя, кроме keep_token. Возвращает число удалённых."""
    stmt = delete(Session).where(Session.user_id == user_id)
    if keep_token:
        stmt = stmt.where(Session.token_hash != _hash(keep_token))
    result = db.execute(stmt.execution_options(synchronize_session=False))
    db.commit()
    return result.rowcount


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        token,
        max_age=int(SESSION_TTL.total_seconds()),
        httponly=True,
        samesite="lax",
        secure=not settings.debug,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="lax", secure=not settings.debug)
