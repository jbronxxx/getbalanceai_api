# Тесты (tests/)

* Стек: `pytest`, `pytest-asyncio`, `httpx.AsyncClient` с `ASGITransport(app=app)`.
* БД — in-memory SQLite (`sqlite+aiosqlite:///:memory:`, `StaticPool`) через фикстуры `setup_test_db`/`db_session` ([conftest.py](conftest.py)); Redis — `fakeredis.aioredis.FakeRedis`; Gemini — `unittest.mock.patch`/фиктивные клиенты, без реальной сети.
* Тесты независимы: данные создаются внутри теста/фикстур.
* Покрытие: happy path (200/201/204 + структура); 422; 401/403 (нет/просроченный токен, чужой `user_id`); 400/404/409 (несуществующее, дубли, лимиты).
* Контракт ответа: `{"status": "success", "data": ...}` или `{"status": "error", "error": {"code", "message"}}`.
* Запрещены: БД на диске (SQLite-файлы), закомментированные тесты, `assert True`, заглушки, `print()`.
* Запускаю сам на двух базах: `pytest tests/ -q` (SQLite в памяти) и с `TEST_DATABASE_URL=postgresql+asyncpg://...` на временной базе Postgres в локальном докере (создать перед прогоном, удалить после — скилл `getbalance-local-backend`). Тест, который проходит только на одной базе, — ошибка, а не повод пропустить.
* Тесты удалённого кода удалять вместе с ним.
