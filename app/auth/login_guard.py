"""Защита входа от подбора пароля.

Два счётчика в БД (переживают перезапуск; обновляются атомарным UPDATE, так что
параллельные запросы в потоках и воркерах считаются точно):
  • «логин|IP» — 5 неудач → блок на 10 минут;
  • «ip|IP»    — 20 неудач в 10-минутном окне (с любыми логинами) → блок IP на 10 минут.
Логин сюда приходит уже нормализованным (app.logins.normalize_login), иначе блок
обходился бы сменой регистра.

Снять блокировки вручную (например, учитель заблокировал себя) — командой
`python -m app.cli unlock` (появится в Task 11): по логину или все сразу.
"""

from datetime import datetime, timedelta

from sqlalchemy import and_, case, delete, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import LoginAttempt
from app.timeutil import utcnow

MAX_FAILURES = 5
BLOCK_FOR = timedelta(minutes=10)
IP_MAX_FAILURES = 20
IP_WINDOW = timedelta(minutes=10)  # фиксированное окно подсчёта: отсчитывается от первой неудачи в окне


def _keys(login: str, ip: str) -> tuple[str, str]:
    return f"{login}|{ip}", f"ip|{ip}"


def blocked_until(db: Session, login: str, ip: str, now: datetime | None = None) -> datetime | None:
    """Позднейший из действующих блоков (по логину+IP и по IP) или None."""
    now = now or utcnow()
    return db.scalar(
        select(func.max(LoginAttempt.blocked_until)).where(
            LoginAttempt.key.in_(_keys(login, ip)), LoginAttempt.blocked_until > now
        )
    )


def is_locked(db: Session, login: str, now: datetime | None = None) -> bool:
    """Заблокирован ли логин хотя бы на одном IP (для значка у учителя). Блок самого IP не считается."""
    now = now or utcnow()
    return (
        db.scalar(
            select(LoginAttempt.key)
            .where(LoginAttempt.key.startswith(f"{login}|", autoescape=True), LoginAttempt.blocked_until > now)
            .limit(1)
        )
        is not None
    )


def _row_exists(db: Session, key: str) -> bool:
    return db.scalar(select(LoginAttempt.key).where(LoginAttempt.key == key)) is not None


def _count_failure(db: Session, key: str, limit: int, now: datetime) -> datetime | None:
    """Атомарно учитывает неудачу по одному ключу; возвращает конец блока ключа (если он есть).

    Всё решает один UPDATE на стороне БД, поэтому параллельные запросы (потоки и воркеры)
    не теряют приращения и не дают лишних попыток сверх лимита.
    """
    if not _row_exists(db, key):
        try:
            db.execute(insert(LoginAttempt).values(key=key, failures=0))
            db.flush()
        except IntegrityError:
            db.rollback()  # строку только что создал параллельный запрос: она нам и нужна

    t = LoginAttempt
    active = and_(t.blocked_until.is_not(None), t.blocked_until > now)
    # новое окно: блок истёк, окна ещё нет или оно длится дольше IP_WINDOW
    new_window = or_(
        t.blocked_until.is_not(None), t.window_started_at.is_(None), t.window_started_at < now - IP_WINDOW
    )
    failures = case((active, t.failures), (new_window, 1), else_=t.failures + 1)
    db.execute(
        update(t)
        .where(t.key == key)
        .values(
            failures=failures,
            window_started_at=case((active, t.window_started_at), (new_window, now), else_=t.window_started_at),
            last_failure_at=case((active, t.last_failure_at), else_=now),
            blocked_until=case((active, t.blocked_until), (failures >= limit, now + BLOCK_FOR), else_=None),
        )
        .execution_options(synchronize_session=False)
    )
    end = db.scalar(select(t.blocked_until).where(t.key == key))
    db.commit()
    return end


def register_failure(db: Session, login: str, ip: str, now: datetime | None = None) -> datetime | None:
    """Учитывает неудачный вход. Возвращает конец блока, если он наступил сейчас или уже действует."""
    now = now or utcnow()
    account_key, ip_key = _keys(login, ip)
    ends = [
        _count_failure(db, account_key, MAX_FAILURES, now),
        _count_failure(db, ip_key, IP_MAX_FAILURES, now),
    ]
    return max((e for e in ends if e), default=None)


def register_success(db: Session, login: str, ip: str) -> None:
    """Успешный вход обнуляет счётчик «логин|IP»; счётчик самого IP не трогает."""
    db.execute(delete(LoginAttempt).where(LoginAttempt.key == f"{login}|{ip}"))
    db.commit()


def unlock(db: Session, login: str) -> int:
    """Снимает блокировки логина со всех IP. Возвращает число удалённых ключей."""
    # «_» в логине — подстановочный знак LIKE, поэтому autoescape (иначе a_b заденет axb)
    result = db.execute(
        delete(LoginAttempt)
        .where(LoginAttempt.key.startswith(f"{login}|", autoescape=True))
        .execution_options(synchronize_session=False)
    )
    db.commit()
    return result.rowcount


def reset_all(db: Session) -> None:
    db.execute(delete(LoginAttempt))
    db.commit()
