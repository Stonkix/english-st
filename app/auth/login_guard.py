"""Защита входа от подбора пароля.

Два счётчика в БД (общие для воркеров, переживают перезапуск):
  • «логин|IP» — 5 неудач → блок на 10 минут;
  • «ip|IP»    — 20 неудач за 10 минут (с любыми логинами) → блок IP на 10 минут.
Логин сюда приходит уже нормализованным (app.logins.normalize_login), иначе блок
обходился бы сменой регистра.

Снять блокировки вручную (например, учитель заблокировал себя) — командой
`python -m app.cli unlock` (появится в Task 11): по логину или все сразу.
"""

from datetime import datetime, timedelta

from sqlalchemy import delete
from sqlalchemy.orm import Session

from app.models import LoginAttempt
from app.timeutil import utcnow

MAX_FAILURES = 5
BLOCK_FOR = timedelta(minutes=10)
IP_MAX_FAILURES = 20
IP_WINDOW = timedelta(minutes=10)  # окно «тишины»: после него счётчик любого ключа начинается с нуля


def _keys(login: str, ip: str) -> tuple[str, str]:
    return f"{login}|{ip}", f"ip|{ip}"


def blocked_until(db: Session, login: str, ip: str, now: datetime | None = None) -> datetime | None:
    """Позднейший из действующих блоков (по логину+IP и по IP) или None."""
    now = now or utcnow()
    ends = [
        row.blocked_until
        for key in _keys(login, ip)
        if (row := db.get(LoginAttempt, key)) and row.blocked_until and row.blocked_until > now
    ]
    return max(ends, default=None)


def _count_failure(db: Session, key: str, limit: int, now: datetime) -> datetime | None:
    """Учитывает неудачу по одному ключу; возвращает конец действующего блока ключа."""
    row = db.get(LoginAttempt, key)
    if row is None:
        row = LoginAttempt(key=key, failures=0)
        db.add(row)
    elif row.blocked_until and row.blocked_until > now:
        return row.blocked_until  # блок уже действует: не продлеваем
    elif (row.last_failure_at and now - row.last_failure_at > IP_WINDOW) or row.blocked_until:
        row.failures, row.blocked_until = 0, None  # давно тихо или блок истёк: считаем заново
    row.failures += 1
    row.last_failure_at = now
    if row.failures >= limit:
        row.blocked_until = now + BLOCK_FOR
    return row.blocked_until


def register_failure(db: Session, login: str, ip: str, now: datetime | None = None) -> datetime | None:
    """Учитывает неудачный вход. Возвращает конец блока, если он наступил сейчас или уже действует."""
    now = now or utcnow()
    account_key, ip_key = _keys(login, ip)
    ends = [
        _count_failure(db, account_key, MAX_FAILURES, now),
        _count_failure(db, ip_key, IP_MAX_FAILURES, now),
    ]
    db.commit()
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
