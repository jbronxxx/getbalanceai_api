# Сервисы (app/services/)

* Конструктор: `__init__(self, db: AsyncSession)`; внешние клиенты (Gemini) мокаемые: `__init__(self, db, client=None)`.
* Промежуточные объекты: `await self.db.flush()` / `refresh(...)`; атомарные составные операции — `UnitOfWork(session_factory)` из `app/database.py`.
* Ошибки — только из `app.exceptions` (`NotFoundException(ErrorCode.TRANSACTION_NOT_FOUND, "...")`, `BadRequestException`, `ConflictException`, `UnauthorizedException`, `ForbiddenException`); без сырых `HTTPException` и исключений БД.
* N+1: `selectinload`/`joinedload`. Списки транзакций/событий — keyset-пагинация по `(date, id)` или `(created_at, id)`.
* Тяжёлые AI-инсайты кэшировать в Redis с TTL; при изменении транзакций — `AIService.invalidate_cache(user_id)`.
* Логгер: `logger = get_logger(__name__)`; не логировать пароли, сырые JWT, API-ключи, секреты.
