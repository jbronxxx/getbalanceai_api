# GetBalance AI API (FastAPI)

Production-grade финансовое API. Источники правды: @ARCHITECTURE.md, @RESPONSE_FORMAT.md. Локальные правила — `CLAUDE.md` в `app/routers/`, `app/services/`, `app/models/`, `tests/`, `migrations/`. Пошаговый процесс задач — skill `task-implementation-workflow`.

## Код
* Надёжность, безопасность, типизация. Без ИИ-меток (`# AI generated`, `# TODO for AI`), костылей/«временных решений», закомментированного кода, `print()`, временных файлов.
* Комментарии/docstrings — кратко, на русском, про механику и предметную область.
* Стиль сразу правильный: flake8 (`.flake8`, строка 125), black `--line-length=125`, isort `--profile=black`, без unused imports.

## Слои (границы не нарушать)
Routers (HTTP, DI, `ApiResponse[T]`) → Schemas (Pydantic v2) → Services (логика, транзакции, предметные ошибки) → Models (SQLAlchemy 2.0 `Mapped`) → Database (`UnitOfWork` / `get_db`).

## Флоу задачи
1. **Ветка** от свежего `main`: `git checkout main && git pull origin main && git checkout -b <feat|fix|refactor|test|docs|chore>/<name>`. Коммиты/пуши в `main` запрещены.
2. **Тесты**: добавить/обновить unit и интеграционные; happy path + негатив (валидация, 401/403/404, лимиты).
3. **Проверки запускает только пользователь.** Самому не выполнять `black`, `isort`, `flake8`, `pytest`, `pre-commit`, `alembic upgrade`, `docker compose`. В конце ответа дать блок команд (лишнее убрать):
   ```bash
   black --line-length=125 app tests
   isort --profile=black app tests
   flake8 app tests
   pytest tests/ -v
   ```
4. **Миграции** (изменены `app/models/`): создать Alembic-миграцию, проверить `upgrade()`/`downgrade()`, добавить в блок `alembic upgrade head`.
5. **Окружение** (изменены `requirements.txt`, `.env*`, `Dockerfile`, `docker-compose.yml`): добавить `docker compose down && docker compose up --build`.
6. **Ревью по диффу**: чистота (нет мусора/меток); архитектура (слои, `ApiResponse[T]`, RESPONSE_FORMAT.md); безопасность (права по `current_user.id`, нет утечек, безопасный SQL); производительность (нет N+1, лишних запросов, утечек сессий).
7. **Документация**: ARCHITECTURE.md (схемы БД, паттерны, сервисы, интеграции), README.md (запуск, env, команды; без дублей с ARCHITECTURE), RESPONSE_FORMAT.md (контракты), TEST_CHECKLIST.md (при добавлении/удалении тестов).
8. **Git**: `git status` — без временных файлов, дампов, SQLite, `__pycache__`, `.pytest_cache`; артефакты задач/промтов/отчётов — только через `.gitignore`. `git commit` — только по явной просьбе, пуш только в текущую ветку.
9. **Клиенты**: если изменились контракты API (эндпоинты, поля DTO, `ErrorCode`) — кратко перечислить в конце ответа; промт для клиента только по запросу.
