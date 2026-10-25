from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")


def utcnow() -> datetime:
    """Текущее время UTC без tzinfo: все даты в БД хранятся как naive UTC."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def moscow_today(now: datetime | None = None) -> date:
    """Сегодняшняя дата по Москве; now — naive UTC (по умолчанию текущее время). День меняется в 00:00 МСК."""
    return (now or utcnow()).replace(tzinfo=timezone.utc).astimezone(MOSCOW).date()


def moscow_str(dt: datetime) -> str:
    """Naive UTC из БД -> «ДД.ММ.ГГГГ ЧЧ:ММ» по Москве."""
    return dt.replace(tzinfo=timezone.utc).astimezone(MOSCOW).strftime("%d.%m.%Y %H:%M")
