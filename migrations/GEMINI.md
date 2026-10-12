# Правила миграций (migrations/)

* Каждое изменение `app/models/` — отдельная миграция Alembic с осмысленным именем: `alembic revision --autogenerate -m "add_category_field_to_transactions"`.
* Вручную проверять файл в `migrations/versions/`: `upgrade()` только с намеренными изменениями; `downgrade()` полностью возвращает схему; корректны ENUM и FK (`ondelete`).
* Production: `nullable=False` на заполненных таблицах — только с `server_default`; удаление колонок/таблиц — после вывода из кода (zero-downtime); индексы на больших таблицах PostgreSQL — `CONCURRENTLY`.
* Агент миграции не применяет; в конце ответа выдаёт команду: `alembic upgrade head` (откат: `alembic downgrade -1`, проверка: `alembic current`).
