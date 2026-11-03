"""Границы календарных периодов в часовом поясе клиента.

Операции хранятся в UTC, а месяц пользователь видит по своим часам: для UTC+5 операция
1-го числа в 03:00 по местному времени — это 22:00 последнего дня прошлого месяца по UTC.
Границы месяца считаются в поясе клиента и переводятся в UTC для запросов к БД.
"""

from datetime import datetime, timedelta, timezone, tzinfo

# Допустимые смещения часовых поясов в минутах: от UTC-12:00 до UTC+14:00
MIN_UTC_OFFSET_MINUTES = -12 * 60
MAX_UTC_OFFSET_MINUTES = 14 * 60


def timezone_from_offset(raw: str | None) -> tzinfo:
    """Часовой пояс по смещению от UTC в минутах (заголовок X-Timezone-Offset); без значения или при ошибке — UTC."""
    if raw is None:
        return timezone.utc
    try:
        minutes = int(raw.strip())
    except ValueError:
        return timezone.utc
    if not MIN_UTC_OFFSET_MINUTES <= minutes <= MAX_UTC_OFFSET_MINUTES:
        return timezone.utc
    return timezone(timedelta(minutes=minutes))


def month_bounds(year: int, month: int, tz: tzinfo = timezone.utc) -> tuple[datetime, datetime]:
    """Начало месяца и начало следующего в поясе tz, переведённые в UTC: [start, end)."""
    start = datetime(year, month, 1, tzinfo=tz)
    end = datetime(year + 1, 1, 1, tzinfo=tz) if month == 12 else datetime(year, month + 1, 1, tzinfo=tz)
    return start.astimezone(timezone.utc), end.astimezone(timezone.utc)


def year_bounds(year: int, tz: tzinfo = timezone.utc) -> tuple[datetime, datetime]:
    """Начало года и начало следующего в поясе tz, переведённые в UTC: [start, end)."""
    return (
        datetime(year, 1, 1, tzinfo=tz).astimezone(timezone.utc),
        datetime(year + 1, 1, 1, tzinfo=tz).astimezone(timezone.utc),
    )


def current_month(tz: tzinfo = timezone.utc) -> tuple[int, int]:
    """Текущие (год, месяц) по часам клиента."""
    now = datetime.now(tz)
    return now.year, now.month


def timezone_key(tz: tzinfo) -> str:
    """Смещение пояса для ETag и логов: ответы для разных поясов различаются."""
    offset = tz.utcoffset(None) or timedelta(0)
    return str(int(offset.total_seconds() // 60))
