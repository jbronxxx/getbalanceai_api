"""Общие зависимости роутеров."""

from datetime import tzinfo

from fastapi import Header

from app.services.period import timezone_from_offset


def get_client_timezone(
    x_timezone_offset: str | None = Header(
        None,
        alias="X-Timezone-Offset",
        description="Смещение часового пояса клиента от UTC в минутах (UTC+5 — 300); без заголовка — UTC",
    ),
) -> tzinfo:
    """Часовой пояс клиента для границ месяцев в бюджетах и AI-отчётах."""
    return timezone_from_offset(x_timezone_offset)
