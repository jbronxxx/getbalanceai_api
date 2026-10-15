# Модели (app/models/)

* SQLAlchemy 2.0: наследование от `Base` (`app.database`), `Mapped[T]` + `mapped_column(...)`.
* PK — UUIDv4: `id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)`.
* Деньги — `Numeric(18, 2)`/`Decimal`, не `Float`; курсы — `Numeric(18, 6)`.
* Даты: `DateTime(timezone=True)`, default `lambda: datetime.now(timezone.utc)`.
* FK всегда с `ondelete`; составные индексы под частые фильтры (`Index("ix_tx_user_date", "user_id", "date")`).
* Enums — `(str, enum.Enum)`.
* При смене схемы: миграция (см. @../../migrations/CLAUDE.md), обновить схему в ARCHITECTURE.md, при необходимости фикстуры `tests/conftest.py`.
