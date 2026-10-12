# Регламент разработки Finance App API (FastAPI)

Production-grade финансовое API. Источник правды: [ARCHITECTURE.md](ARCHITECTURE.md), [RESPONSE_FORMAT.md](RESPONSE_FORMAT.md). Локальные правила: `app/routers/`, `app/services/`, `app/models/`, `tests/`, `migrations/` (`GEMINI.md` в каждой).

## 1. Правила кода
* Надёжность, безопасность, типизация. Без ИИ-меток (`# AI generated`, `# Temporary fix`, `# TODO for AI`), закомментированного кода, отладочных `print()`, временных файлов.
* Комментарии и docstrings — кратко, на русском, по предметной области.
* Стиль: flake8 (`.flake8`, строка 125, игнор E203/W503), black (`--line-length=125`), isort (`--profile=black`). Без unused imports. Пишите чисто сразу.

## 2. Слои
Routers (HTTP, DI, `ApiResponse[T]`) → Schemas (Pydantic v2) → Services (бизнес-логика, транзакции, предметные ошибки) → Models (SQLAlchemy 2.0 `Mapped`) → Database (`UnitOfWork` / `get_db`). Границы слоёв не нарушать.

## 3. Флоу задачи
1. **Ветка** от свежего `main`: `git checkout main && git pull origin main && git checkout -b <type>/<name>`; типы: `feat|fix|refactor|test|docs|chore`. Коммиты в `main` запрещены.
2. **Код** по границам слоёв и локальным правилам.
3. **Тесты**: обновить/добавить unit и интеграционные в `tests/`; happy path + негатив (валидация, 401/403/404, лимиты).
4. **Проверки запускает только пользователь.** Агенту запрещено самому выполнять `black`, `isort`, `flake8`, `pytest`, `pre-commit`, `alembic upgrade`, `docker compose`. В конце ответа выдать блок команд для ручного запуска (лишние убрать):
   ```bash
   black --line-length=125 app tests
   isort --profile=black app tests
   flake8 app tests
   pytest tests/ -v
   ```
5. **Миграции** (изменены `app/models/`): создать Alembic-миграцию, проверить `upgrade()`/`downgrade()`; добавить в блок команд `alembic upgrade head`.
6. **Окружение** (изменены `requirements.txt`, `.env*`, `Dockerfile`, `docker-compose.yml`): добавить в блок команд `docker compose down && docker compose up --build`.
7. **Ревью (Senior Reviewer)** по диффу:
   * чистота: нет мусора, ИИ-меток, отладочных принтов;
   * архитектура: границы слоёв, `ApiResponse[T]`, контракты RESPONSE_FORMAT.md;
   * безопасность: права по `current_user.id`, нет утечек, безопасный SQL;
   * производительность: нет N+1, лишних обращений к БД, утечек сессий.
8. **Документация**: ARCHITECTURE.md (архитектура/модели/сервисы/интеграции), README.md (запуск, env, команды), RESPONSE_FORMAT.md (контракты), TEST_CHECKLIST.md (новые сценарии).
9. **Git**: `git status` — без временных файлов, дампов, SQLite, `__pycache__`, `.pytest_cache`.
10. **Фронтенд/клиенты**: если изменены контракты API (эндпоинты, поля DTO, `ErrorCode`) — кратко перечислить в конце ответа. Промт для клиента — только по запросу пользователя.
