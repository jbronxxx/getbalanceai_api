# GetBalance AI API (FastAPI)

Production-grade финансовое API. Источники правды: @ARCHITECTURE.md, @RESPONSE_FORMAT.md. Локальные правила — `CLAUDE.md` в `app/routers/`, `app/services/`, `app/models/`, `tests/`, `migrations/`. Пошаговый процесс задач — skill `task-implementation-workflow`.

## Код
* Надёжность, безопасность, типизация. Без ИИ-меток (`# AI generated`, `# TODO for AI`), костылей/«временных решений», закомментированного кода, `print()`, временных файлов.
* Комментарии/docstrings — кратко, на русском, про механику и предметную область.
* Стиль сразу правильный: flake8 (`.flake8`, строка 125), black `--line-length=125`, isort `--profile=black`, без unused imports.
* **Общие помощники, а не копии.** Повторяющуюся логику выносить в одну функцию и вызывать из всех мест: ответ 304 по ETag — `not_modified_response` (`app/routers/dependencies.py`), проверка «категория подходит к типу операции» — `_ensure_category_fits_type` (`app/schemas/schemas.py`). Перед новым кодом искать готовый помощник в `app/routers/dependencies.py`, `app/services/`, `app/schemas/`.
* **Клиент ещё не выпущен**: совместимость со старыми версиями мобильного приложения (записи для старых клиентов, фильтры «на случай старого клиента», проверки удалённых значений) не делать, пока пользователь не скажет обратное. Миграции данных в БД — нужны (прод-база с данными есть). Язык по умолчанию и для неподдерживаемой локали — `en`.
* **Языки**: всё, что видит пользователь, — на языке его интерфейса: тексты приложения, юридические страницы, промпт и данные для ИИ (чтобы и ответ был на этом языке), сообщения об ошибках (приложение берёт их из l10n по коду, текст сервера не показывает). Комментарии, внутренняя документация (включая Swagger), исключения, логи, Grafana и Prometheus — на русском.
* **Мёртвый код удалять сразу.** Если правка оставляет функции, схемы, `ErrorCode`, поля или настройки без использования — удалять в той же ветке вместе с их тестами; живые проверки переносить. Удаление колонок БД — по правилам `migrations/CLAUDE.md`.

## Слои (границы не нарушать)
Routers (HTTP, DI, `ApiResponse[T]`) → Schemas (Pydantic v2) → Services (логика, транзакции, предметные ошибки) → Models (SQLAlchemy 2.0 `Mapped`) → Database (`UnitOfWork` / `get_db`).

## Флоу задачи
1. **Ветка** от свежего `main`: `git checkout main && git pull origin main && git checkout -b <feat|fix|refactor|test|docs|chore>/<name>`. Коммиты/пуши в `main` запрещены.
2. **Тесты**: добавить/обновить unit и интеграционные; happy path + негатив (валидация, 401/403/404, лимиты).
3. **Проверки запускаю сам** (`.venv`, локальный докер — скилл `getbalance-local-backend`) и в конце ответа даю фактические результаты:
   ```bash
   black --line-length=125 app tests && isort --profile=black app tests && flake8 app tests
   pytest tests/ -q                                   # SQLite в памяти
   TEST_DATABASE_URL=postgresql+asyncpg://... pytest tests/ -q   # временная база Postgres, после — удалить
   ```
   Тесты гонять на обеих базах: только Postgres проверяет длину строк, `Numeric`, ENUM и диалектный SQL.
4. **Миграции** (изменены `app/models/`): создать Alembic-миграцию и проверить на локальной базе в докере: `upgrade head` → `downgrade -1` → `upgrade head` → `alembic check` (нет расхождений моделей и схемы).
5. **Окружение** (изменены `requirements.txt`, `.env*`, `Dockerfile`, `docker-compose.yml`): пересобрать локально `docker compose up -d --build`, проверить `/health`. Прод не трогать — только по явной просьбе.
6. **Ревью по диффу**: чистота (нет мусора/меток); архитектура (слои, `ApiResponse[T]`, RESPONSE_FORMAT.md); безопасность (права по `current_user.id`, нет утечек, безопасный SQL); производительность (нет N+1, лишних запросов, утечек сессий).
7. **Документация**: ARCHITECTURE.md (схемы БД, паттерны, сервисы, интеграции), README.md (запуск, env, команды; без дублей с ARCHITECTURE), RESPONSE_FORMAT.md (контракты), TEST_CHECKLIST.md (при добавлении/удалении тестов).
8. **Git**: `git status` — без временных файлов, дампов, SQLite, `__pycache__`, `.pytest_cache`; артефакты задач/промтов/отчётов — только через `.gitignore`. `git commit` — только по явной просьбе (pre-commit гоняет black/isort/flake8), пуш только в текущую ветку.
9. **Клиенты**: если изменились контракты API (эндпоинты, поля DTO, `ErrorCode`) — кратко перечислить в конце ответа; промт для клиента только по запросу.
