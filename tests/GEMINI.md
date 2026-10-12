# Правила тестов (tests/)

* Стек: `pytest`, `pytest-asyncio`, `httpx.AsyncClient` с `ASGITransport(app=app)`.
* БД — in-memory SQLite (`sqlite+aiosqlite:///:memory:`, `StaticPool`) через фикстуры `setup_test_db`/`db_session` в [conftest.py](conftest.py); Redis — `fakeredis.aioredis.FakeRedis`; Gemini — `unittest.mock.patch`/фиктивные клиенты, без реальных сетевых запросов.
* Тесты независимы: данные создаются внутри теста/фикстур, без зависимости от порядка.
* Покрытие: happy path (200/201/204, структура данных); валидация 422; 401/403 (нет/просроченный токен, чужой `user_id`); 400/404/409 (несуществующее, дубли, лимиты).
* Ответы проверять на контракт `ApiResponse[T]` (`{"status": "success", "data": ...}`) или `ErrorResponse` (`{"status": "error", "error": {"code", "message"}}`).
* Запрещены: файлы БД на диске, закомментированные тесты, `assert True`, пустые заглушки, `print()`.
* Агент тесты не запускает; в конце выдаёт команду `pytest tests/ -v` (или `pytest tests/test_<module>.py -v`).
