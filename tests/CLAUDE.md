# Тесты (tests/)

* Стек: `pytest`, `pytest-asyncio`, `httpx.AsyncClient` с `ASGITransport(app=app)`.
* БД — in-memory SQLite (`sqlite+aiosqlite:///:memory:`, `StaticPool`) через фикстуры `setup_test_db`/`db_session` ([conftest.py](conftest.py)); Redis — `fakeredis.aioredis.FakeRedis`; Gemini — `unittest.mock.patch`/фиктивные клиенты, без реальной сети.
* Тесты независимы: данные создаются внутри теста/фикстур.
* Покрытие: happy path (200/201/204 + структура); 422; 401/403 (нет/просроченный токен, чужой `user_id`); 400/404/409 (несуществующее, дубли, лимиты).
* Контракт ответа: `{"status": "success", "data": ...}` или `{"status": "error", "error": {"code", "message"}}`.
* Запрещены: БД на диске, закомментированные тесты, `assert True`, заглушки, `print()`.
* Не запускать самому; выдать `pytest tests/ -v` (или `pytest tests/test_<module>.py -v`).
