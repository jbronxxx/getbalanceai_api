"""Общие зависимости и помощники роутеров."""

from datetime import tzinfo

from fastapi import Header, Response, status

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


def not_modified_response(if_none_match: str | None, etag: str) -> Response | None:
    """Ответ 304, если ETag из If-None-Match совпадает с текущим (RFC 7232); иначе None.

    Заголовок может содержать несколько ETag через запятую, слабые (W/"...") и «*».
    """
    if not if_none_match:
        return None
    client_etags = [tag.strip().removeprefix("W/").strip('"') for tag in if_none_match.split(",")]
    if etag not in client_etags and "*" not in client_etags:
        return None
    response = Response(status_code=status.HTTP_304_NOT_MODIFIED)
    response.headers["ETag"] = f'"{etag}"'
    return response
