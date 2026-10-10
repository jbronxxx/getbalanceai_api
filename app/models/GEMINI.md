# Регламент слоя моделей базы данных (app/models/GEMINI.md)

Данные правила применяются при работе с моделями данных SQLAlchemy в директории `app/models/`.

---

## 1. Назначение слоя Models

Модели определяют реляционную схему базы данных PostgreSQL, ограничения целостности (constraints), индексы и связи между сущностями.

---

## 2. Стандарты реализации SQLAlchemy 2.0

* **Базовый класс**: Все модели наследуются от `Base` из `app.database`.
* **Типизация**: Использовать современный синтаксис `Mapped[T]` и `mapped_column(...)`.
* **Первичные ключи (Primary Keys)**:
  * Использовать UUIDv4 для всех таблиц:
    ```python
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    ```
* **Финансовые данные и суммы**:
  * Категорически запрещено использовать `Float` для денег.
  * Все денежные поля задаются типом `Numeric(12, 2)` или `Decimal`.
* **Временные метки (Timestamps)**:
  * Все даты хранятся с таймзоной: `DateTime(timezone=True)`.
  * Дефолтные значения задаются через UTC: `default=lambda: datetime.now(timezone.utc)`.
* **Индексы и внешние ключи (Foreign Keys)**:
  * Для всех внешних ключей задавать поведение при удалении: `ForeignKey("users.id", ondelete="CASCADE")`.
  * Создавать составные индексы для частых запросов фильтрации и сортировки (например, `Index("ix_tx_user_date", "user_id", "date")`).
* **Enums**:
  * Наследовать от `(str, enum.Enum)` для прозрачной сериализации в JSON.

---

## 3. Обязательная синхронизация

При добавлении нового поля, таблицы или индекса:
1. Создать миграцию Alembic (см. [migrations/GEMINI.md](file:///c:/Projects/getbalanceai_api/migrations/GEMINI.md)).
2. Обновить ER-диаграмму и описание схемы в [ARCHITECTURE.md](file:///c:/Projects/getbalanceai_api/ARCHITECTURE.md).
3. Обновить фабрики/фикстуры тестовых данных в `tests/conftest.py` при необходимости.
