# Стандартный формат API ответов

## Обзор

API использует единообразный формат ответов для всех эндпоинтов:

- **Успешные ответы**: `ApiResponse[T]` — обертка с данными результата (`data`)
- **Пагинированные ответы**: `ApiResponse[PaginatedResponse[T]]` — обертка со страницами списков (`items`, `total`, `limit`, `offset`)
- **Ошибочные ответы**: `ErrorResponse` — фиксированный контракт с машиночитаемым строковым кодом (`code`), понятным описанием (`message`) и опциональными деталями (`details`)
- **Простые ответы**: `BaseResponse` — для операций без значимых возвращаемых данных (например, logout, delete)

---

## Успешные ответы

### Формат: `ApiResponse[T]`

```json
{
  "status": "success",
  "data": { /* Данные ответа */ },
  "message": "Опциональное сообщение"
}
```

### Формат пагинации: `ApiResponse[PaginatedResponse[T]]`

```json
{
  "status": "success",
  "data": {
    "items": [ /* Список элементов страницы */ ],
    "total": 120,
    "limit": 50,
    "offset": 0
  }
}
```

### Примеры:

#### Регистрация пользователя (POST /api/v1/auth/register)

```json
{
  "status": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "name": "John Doe",
    "avatar_url": null,
    "base_currency": "RUB",
    "created_at": "2026-09-10T10:30:00Z"
  }
}
```

#### Обновление профиля пользователя (PATCH /api/v1/auth/me)

```json
{
  "status": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "name": "John Doe",
    "avatar_url": "https://example.com/avatar.png",
    "base_currency": "UZS",
    "created_at": "2026-09-10T10:30:00Z"
  }
}
```

#### Создание транзакции (POST /api/v1/transactions/)

```json
{
  "status": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440001",
    "amount": 1500.50,
    "currency": "RUB",
    "exchange_rate": 1.0,
    "base_amount": 1500.50,
    "description": "Зарплата",
    "category": "salary",
    "type": "income",
    "date": "2026-09-10T10:00:00Z",
    "created_at": "2026-09-10T10:30:00Z"
  }
}
```

#### Котировки валют (GET /api/v1/currencies/rates)

```json
{
  "status": "success",
  "data": {
    "base": "USD",
    "rates": {
      "USD": 1.0,
      "EUR": 0.92,
      "RUB": 96.5,
      "UZS": 12850.0,
      "KZT": 490.0
    },
    "updated_at": "2026-10-11T19:00:00Z"
  }
}
```

#### Список бюджетов с мультивалютным расчетом (GET /api/v1/budgets)

*Заголовки ответа:* `ETag: "9b64bc7a049d5fa9e1d8849bbfbc4a26"` (при совпадении с `If-None-Match` возвращается статус `304 Not Modified`).

```json
{
  "status": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440002",
      "category": "food",
      "limit_amount": 500.00,
      "month": 10,
      "year": 2026,
      "spent": 250.00,
      "remaining": 250.00
    }
  ]
}
```

#### Пакетная синхронизация (POST /api/v1/sync)

*Идемпотентный ответ с синхронизированными и обогащенными сущностями:*

```json
{
  "status": "success",
  "data": {
    "synced_transactions": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "amount": 1280000.00,
        "currency": "UZS",
        "exchange_rate": 0.000078,
        "base_amount": 100.00,
        "description": "Ужин",
        "category": "food",
        "type": "expense",
        "date": "2026-10-06T12:00:00Z",
        "created_at": "2026-10-06T12:00:00Z"
      }
    ],
    "synced_budgets": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440002",
        "category": "food",
        "limit_amount": 500.00,
        "month": 10,
        "year": 2026,
        "spent": 100.00,
        "remaining": 400.00
      }
    ]
  }
}
```

---

## Ошибочные ответы (Единый контракт)

### Формат: `ErrorResponse`

Любая ошибка сервера (400 Bad Request, 401 Unauthorized, 403 Forbidden, 404 Not Found, 422 ValidationError, 500 Internal Server Error) возвращается в единой структуре:

```json
{
  "status": "error",
  "code": "BUDGET_LIMIT_EXCEEDED",
  "message": "Превышен лимит бюджета для данной категории",
  "details": { /* Опциональные структурированные данные */ }
}
```

### Поля контракта ошибки:
- **`status`** (*строка, всегда `"error"`*): Сигнализирует о неуспешном результате.
- **`code`** (*строка*): Уникальный машиночитаемый код ошибки (SCREAMING_SNAKE_CASE). Позволяет клиенту реализовывать точную логику и локализацию интерфейса без парсинга строк.
- **`message`** (*строка*): Понятное человекочитаемое описание проблемы на русском языке.
- **`details`** (*объект, опционально*): Дополнительные параметры (например, список невалидных полей, ID сущности и т.д.).

---

## Реестр кодов ошибок (`ErrorCode`)

| Код ошибки | HTTP-код | Описание |
| :--- | :--- | :--- |
| `VALIDATION_ERROR` | 422 | Ошибка валидации параметров запроса Pydantic (содержит `details.fields`) |
| `INVALID_CREDENTIALS` | 401 | Неверный email или пароль при логине |
| `USER_ALREADY_EXISTS` | 400 | Пользователь с таким email уже зарегистрирован |
| `USER_NOT_FOUND` | 401 / 404 | Пользователь не найден |
| `EXPIRED_TOKEN` | 401 | Истек срок действия JWT access или refresh токена |
| `INVALID_TOKEN` | 401 | Некорректная подпись, структура или тип JWT токена |
| `TOKEN_REVOKED` | 401 | Токен был отозван (например, после logout или ротации) |
| `TOKEN_NOT_FOUND` | 404 | Токен не найден в БД для деактивации |
| `TRANSACTION_NOT_FOUND` | 404 | Транзакция с указанным ID не найдена |
| `BUDGET_NOT_FOUND` | 404 | Бюджет с указанным ID/категорией не найден |
| `BUDGET_LIMIT_EXCEEDED` | 400 | Превышен установленный лимит бюджета по категории |
| `SYNC_FAILED` | 400 / 500 | Ошибка при выполнении пакетной офлайн-синхронизации |
| `BAD_REQUEST` | 400 | Общая ошибка некорректного запроса |
| `UNAUTHORIZED` | 401 | Требуется авторизация |
| `FORBIDDEN` | 403 | Отсутствует токен доступа или доступ к ресурсу запрещен |
| `NOT_FOUND` | 404 | Запрашиваемый ресурс или URL не найден |
| `RATE_LIMIT_EXCEEDED` | 429 | Превышен лимит частоты запросов (Rate Limiting) |
| `INTERNAL_SERVER_ERROR` | 500 | Непредвиденная внутренняя ошибка сервера |
| `SERVICE_UNAVAILABLE` | 503 | Сервис или база данных временно недоступны |


---

### Примеры ошибок:

#### 1. Ошибка валидации формы (422 Unprocessable Entity)

```json
{
  "status": "error",
  "code": "VALIDATION_ERROR",
  "message": "Ошибка валидации входных данных",
  "details": {
    "fields": {
      "email": "value is not a valid email address",
      "password": "String should have at least 6 characters"
    }
  }
}
```

#### 2. Неверные учетные данные (401 Unauthorized)

```json
{
  "status": "error",
  "code": "INVALID_CREDENTIALS",
  "message": "Неверный email или пароль"
}
```

#### 3. Истекший токен (401 Unauthorized)

```json
{
  "status": "error",
  "code": "EXPIRED_TOKEN",
  "message": "Срок действия токена доступа истек"
}
```

#### 4. Ресурс не найден (404 Not Found)

```json
{
  "status": "error",
  "code": "TRANSACTION_NOT_FOUND",
  "message": "Транзакция не найдена",
  "details": {
    "transaction_id": "550e8400-e29b-41d4-a716-446655440001"
  }
}
```

#### 5. Превышение лимита бюджета (400 Bad Request)

```json
{
  "status": "error",
  "code": "BUDGET_LIMIT_EXCEEDED",
  "message": "Превышен лимит бюджета для данной категории"
}
```

#### 6. Превышение лимита запросов (429 Too Many Requests)

```json
{
  "status": "error",
  "code": "RATE_LIMIT_EXCEEDED",
  "message": "Превышен лимит запросов. Пожалуйста, повторите попытку позже.",
  "details": {
    "limit": "5 per 1 minute"
  }
}
```

---


## Простые ответы

### Формат: `BaseResponse`

Используется для операций, которые не возвращают сущностей:

```json
{
  "status": "success",
  "message": "Операция выполнена успешно"
}
```

---

## Использование в коде бэкенда

Вместо прямых `raise HTTPException(...)` используйте специализированные исключения из `app.exceptions`:

```python
from app.exceptions import BadRequestException, NotFoundException, UnauthorizedException, ErrorCode

# Сущность не найдена
raise NotFoundException(
    code=ErrorCode.TRANSACTION_NOT_FOUND,
    message="Транзакция не найдена",
    details={"transaction_id": str(transaction_id)},
)

# Ошибка авторизации
raise UnauthorizedException(
    code=ErrorCode.INVALID_CREDENTIALS,
    message="Неверный email или пароль",
)

# Ошибка бизнес-правил
raise BadRequestException(
    code=ErrorCode.USER_ALREADY_EXISTS,
    message="Пользователь с таким email уже зарегистрирован",
)
```
