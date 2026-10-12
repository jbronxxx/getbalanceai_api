# Правила сервисов (app/services/)

* Конструктор: `__init__(self, db: AsyncSession)`; внешние клиенты (Gemini) — с возможностью мока: `__init__(self, db, client=None)`.
* Промежуточные объекты: `await self.db.flush()` / `refresh(...)`; составные атомарные операции — `UnitOfWork(session_factory)` из `app/database.py`.
* Ошибки — только типизированные из `app.exceptions` (`NotFoundException(ErrorCode.TRANSACTION_NOT_FOUND, "...")`, `BadRequestException`, `ConflictException`, `UnauthorizedException`, `ForbiddenException`); без сырых `HTTPException` и исключений БД.
* N+1: `selectinload`/`joinedload` для связей. Списки транзакций/событий — keyset-пагинация по `(date, id)` или `(created_at, id)`.
* Тяжёлые AI-инсайты кэшировать в Redis с TTL; при изменении транзакций вызывать `AIService.invalidate_cache(user_id)`.
* Логгер: `logger = get_logger(__name__)`; не логировать пароли, сырые JWT, API-ключи, секреты.
