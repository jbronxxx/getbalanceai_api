# Миграции (migrations/)

* Каждое изменение `app/models/` — отдельная миграция с осмысленным именем: `alembic revision --autogenerate -m "add_category_field_to_transactions"`.
* Проверять вручную `migrations/versions/`: `upgrade()` — только намеренные изменения; `downgrade()` полностью возвращает схему; корректны ENUM и FK (`ondelete`).
* Production: `nullable=False` на заполненных таблицах — только с `server_default`; удаление колонок/таблиц — после вывода из кода; индексы на больших таблицах — `CONCURRENTLY`.
* Не применять самому; выдать `alembic upgrade head` (откат `alembic downgrade -1`, проверка `alembic current`).
