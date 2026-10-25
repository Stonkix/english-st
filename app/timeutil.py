from datetime import datetime, timezone


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo: все даты в БД хранятся как naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)
