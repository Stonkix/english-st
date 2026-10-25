from datetime import datetime, timezone
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo: все даты в БД хранятся как naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def moscow_str(dt: datetime) -> str:
    """Naive UTC из БД -> «ДД.ММ.ГГГГ ЧЧ:ММ» по Москве."""
    return dt.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
