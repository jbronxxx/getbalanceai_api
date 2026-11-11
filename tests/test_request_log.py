"""Тесты журнала HTTP-запросов (logger/request_log.py): состав записи и отсутствие секретов и личных данных."""

import json
import logging
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from jwt.exceptions import InvalidTokenError
from starlette.requests import Request
from starlette.responses import Response

from app.models.models import User
from logger.request_log import (
    MAX_BODY_CHARS,
    MAX_CLIENT_CHARS,
    MAX_HEADER_CHARS,
    _error_body,
    log_request,
)


def _records(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Записи журнала запросов в разобранном виде."""
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == "request"]


def _text(caplog: pytest.LogCaptureFixture) -> str:
    """Весь текст журнала запросов одной строкой — для поиска утечек."""
    return "\n".join(record.getMessage() for record in caplog.records if record.name == "request")


class TestRequestLog:
    """Запись о каждом запросе: метаданные, заголовки из белого списка, тело только у ответа с ошибкой."""

    @pytest.mark.asyncio
    async def test_success_record(
        self, client: AsyncClient, auth_headers: dict, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Успешный запрос: метод, путь, шаблон маршрута, статус, время, пользователь; без тела и токена."""
        user_id, email = str(test_user.id), test_user.email
        headers = {
            **auth_headers,
            "X-Request-ID": "trace-ok-1",
            "Accept-Language": "ru",
            "Cookie": "session=secret-cookie",
        }
        with caplog.at_level(logging.INFO, logger="request"):
            response = await client.get("/api/v1/budgets/", params={"month": 11, "year": 2026}, headers=headers)
        assert response.status_code == 200

        [record] = _records(caplog)
        assert record["type"] == "request"
        assert record["level"] == "INFO"
        assert record["request_id"] == "trace-ok-1"
        assert record["method"] == "GET"
        assert record["path"] == "/api/v1/budgets/"
        assert record["route"] == "/api/v1/budgets/"
        assert record["query"] == "month=11&year=2026"
        assert record["status"] == 200
        assert record["duration_ms"] >= 0
        assert record["user_id"] == user_id
        assert record["headers"]["accept-language"] == "ru"
        assert "error" not in record
        caplog_record = next(r for r in caplog.records if r.name == "request")
        assert caplog_record.levelno == logging.INFO

        text = _text(caplog)
        assert auth_headers["Authorization"].split()[1] not in text
        assert "secret-cookie" not in text
        assert email not in text

    @pytest.mark.asyncio
    async def test_route_template_instead_of_ids(
        self, client: AsyncClient, auth_headers: dict, caplog: pytest.LogCaptureFixture
    ):
        """Для пути с ID в журнале есть шаблон маршрута — по нему группируются запросы в Grafana."""
        with caplog.at_level(logging.INFO, logger="request"):
            response = await client.delete("/api/v1/transactions/00000000-0000-0000-0000-000000000001", headers=auth_headers)
        assert response.status_code == 404

        [record] = _records(caplog)
        assert record["path"] == "/api/v1/transactions/00000000-0000-0000-0000-000000000001"
        assert record["route"] == "/api/v1/transactions/{transaction_id}"
        assert record["level"] == "WARNING"
        assert record["error"]["code"] == "TRANSACTION_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_validation_error_logs_response_not_request(
        self, client: AsyncClient, auth_headers: dict, caplog: pytest.LogCaptureFixture
    ):
        """422: в журнале ответ с полем ошибки, но не тело запроса (описание, сумма); клиент получает ответ целиком."""
        payload = {"amount": -1, "description": "Подарок маме на юбилей", "category": "food", "type": "expense"}
        with caplog.at_level(logging.INFO, logger="request"):
            response = await client.post("/api/v1/transactions/", json=payload, headers=auth_headers)
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"

        [record] = _records(caplog)
        assert record["status"] == 422
        assert record["error"] == response.json()
        assert "Подарок маме" not in _text(caplog)

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode", side_effect=InvalidTokenError("bad"))
    async def test_login_token_not_logged(self, _decode, _jwk, client: AsyncClient, caplog: pytest.LogCaptureFixture):
        """Неудачный вход: ID-токен Google из тела запроса в журнал не попадает, пользователя нет."""
        with caplog.at_level(logging.INFO, logger="request"):
            response = await client.post("/api/v1/auth/google", json={"id_token": "google-secret-id-token"})
        assert response.status_code == 401

        [record] = _records(caplog)
        assert record["status"] == 401
        assert record["user_id"] is None
        assert "google-secret-id-token" not in _text(caplog)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", ["/health", "/metrics"])
    async def test_service_paths_skipped(self, client: AsyncClient, caplog: pytest.LogCaptureFixture, path: str):
        """Опросы healthcheck и Prometheus не пишутся в журнал."""
        with caplog.at_level(logging.INFO, logger="request"):
            response = await client.get(path)
        assert response.status_code == 200
        assert _records(caplog) == []

    @pytest.mark.asyncio
    async def test_unhandled_exception_logged_as_500(self, caplog: pytest.LogCaptureFixture):
        """Необработанное исключение записывается как 500 уровня ERROR и пробрасывается дальше."""

        async def failing_call_next(_request: Request) -> Response:
            raise RuntimeError("сбой")

        request = Request({"type": "http", "method": "POST", "path": "/api/v1/sync/", "headers": [], "query_string": b""})
        with caplog.at_level(logging.INFO, logger="request"):
            with pytest.raises(RuntimeError):
                await log_request(request, failing_call_next)

        [record] = _records(caplog)
        assert record["status"] == 500
        assert record["level"] == "ERROR"
        assert "error" not in record

    @pytest.mark.asyncio
    async def test_client_fields(self, client: AsyncClient, caplog: pytest.LogCaptureFixture):
        """Данные клиента из заголовков приложения — отдельным полем client; без них поля нет."""
        headers = {
            "X-App-Version": "1.2.3+42",
            "X-Platform": "Android 15; API 35",
            "X-Device-Model": "samsung SM-S921B",
            "X-App-Language": "ru",
        }
        with caplog.at_level(logging.INFO, logger="request"):
            await client.get("/privacy", headers=headers)
            await client.get("/privacy")

        with_client, without_client = _records(caplog)
        assert with_client["client"] == {
            "app_version": "1.2.3+42",
            "platform": "Android 15; API 35",
            "device_model": "samsung SM-S921B",
            "language": "ru",
        }
        assert "client" not in without_client

    @pytest.mark.asyncio
    async def test_long_header_values_truncated(self, client: AsyncClient, caplog: pytest.LogCaptureFixture):
        """Длинные значения заголовков обрезаются: клиент не раздует запись журнала."""
        headers = {"User-Agent": "u" * 1000, "X-Device-Model": "m" * 1000}
        with caplog.at_level(logging.INFO, logger="request"):
            await client.get("/privacy", headers=headers)

        [record] = _records(caplog)
        assert record["headers"]["user-agent"] == "u" * MAX_HEADER_CHARS + "…"
        assert record["client"]["device_model"] == "m" * MAX_CLIENT_CHARS + "…"

    def test_long_error_body_truncated(self):
        """Длинное тело ответа с ошибкой обрезается до предела; короткий JSON пишется объектом."""
        long_body = ("x" * (MAX_BODY_CHARS + 100)).encode()
        truncated = _error_body(long_body)
        assert isinstance(truncated, str)
        assert len(truncated) == MAX_BODY_CHARS + 1
        assert truncated.endswith("…")
        assert _error_body(b'{"code": "NOT_FOUND"}') == {"code": "NOT_FOUND"}
        assert _error_body(b"plain text") == "plain text"
