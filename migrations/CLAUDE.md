# Миграции (migrations/)

* Каждое изменение `app/models/` — отдельная миграция с осмысленным именем: `alembic revision --autogenerate -m "add_category_field_to_transactions"`.
* Проверять вручную `migrations/versions/`: `upgrade()` — только намеренные изменения; `downgrade()` полностью возвращает схему; корректны ENUM и FK (`ondelete`).
* Production: `nullable=False` на заполненных таблицах — только с `server_default`; удаление колонок/таблиц — после вывода из кода; индексы на больших таблицах — `CONCURRENTLY`.
* Проверять самому на локальной базе в докере: `alembic upgrade head` → `alembic downgrade -1` → `alembic upgrade head` → `alembic check` (`No new upgrade operations detected`); схему смотреть через `psql \d <таблица>`. Команды — в скилле `getbalance-local-backend`.
* Если `downgrade()` намеренно пустой (например, значения ENUM в Postgres не удаляются) — так и написать в docstring миграции.
* На прод миграции не применять — только по явной просьбе.
