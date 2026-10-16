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
    "base_currency": "EUR",
    "created_at": "2026-09-10T10:30:00Z"
  }
}
```

#### Вход или регистрация через Google (POST /api/v1/auth/google)

**Тело запроса (`GoogleSignInRequest`):**
```json
{
  "id_token": "eyJhbGciOiJSUzI1NiIs...",
  "avatar_url": "https://lh3.googleusercontent.com/avatar.png",
  "name": "John Doe"
}
```

**Успешный ответ (`ApiResponse[TokenResponse]`):**
```json
{
  "status": "success",
  "data": {
    "access_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "refresh_token": "eyJhbGciOiJIUzI1NiIsIn...",
    "token_type": "bearer",
    "user": {
      "id": "550e8400-e29b-41d4-a716-446655440000",
      "email": "user@example.com",
      "name": "John Doe",
      "avatar_url": "https://lh3.googleusercontent.com/avatar.png",
      "base_currency": "EUR",
      "created_at": "2026-09-10T10:30:00Z"
    }
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
    "currency": "EUR",
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

##### Мультивалютные правила для транзакций (`POST /api/v1/transactions` и `POST /api/v1/sync`):
1. **Валидация валюты (`currency`)**: разрешены только поддерживаемые ISO-коды (`USD`, `EUR`, `KZT`, `UZS`). Любой иной код отклоняется со статусом `422 Unprocessable Entity` (`code: VALIDATION_ERROR`).
2. **Значение по умолчанию**: если поле `currency` не передано или передано как `null`, сервер автоматически устанавливает базовую валюту профиля текущего пользователя (`users.base_currency`).
3. **Единое согласование сумм и курсов (`resolve_amounts`)**:
   - При совпадении валюты операции с базовой валютой пользователя: строго `exchange_rate = 1.0` и `base_amount = amount`. Любые переданные клиентом курсы или `base_amount` игнорируются для защиты от искажения финансовых данных.
   - При мультивалютной операции: клиентский `exchange_rate` принимается только при условии, что его отклонение от серверного кросс-курса не превышает лимит `CLIENT_RATE_TOLERANCE_PERCENT` (по умолчанию 15%). Иначе применяется серверный кросс-курс котировок.
   - Эквивалент в базовой валюте (`base_amount`) всегда рассчитывается сервером по формуле `amount * exchange_rate`, квантованной до 2 знаков (`0.01`). Клиентский `base_amount` используется сервером только для аудита и сверки.

#### Список транзакций с курсорной пагинацией (GET /api/v1/transactions)

*Заголовки ответа:* `ETag: "8f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c"` (отдается только на первой странице выборки без параметра `cursor`).

*Правила условного кэширования и пагинации:*
1. **Глобальный ETag коллекции:** ETag рассчитывается по всей коллекции транзакций пользователя `hash(user_id, count, max(created_at), since)` и не зависит от размера страницы (`limit`).
2. **Изоляция пагинации:** При переходе по курсору (`cursor != null`) заголовок `ETag` не выставляется, а заголовок `If-None-Match` игнорируется (сервер никогда не возвращает 304 для страниц пагинации).
3. **Ответ 304 Not Modified:** При повторном запросе первой страницы с валидным `If-None-Match` сервер возвращает HTTP 304 с обязательным сохранением заголовка `ETag`.

#### Котировки валют (GET /api/v1/currencies/rates)

*Заголовки ответа:* `Cache-Control: public, max-age=3600` (клиентское и прокси-кэширование котировок).

```json
{
  "status": "success",
  "data": {
    "base": "USD",
    "rates": {
      "USD": 1.0,
      "EUR": 0.92,
      "EUR": 0.92,
      "UZS": 12850.0,
      "KZT": 490.0
    },
    "updated_at": "2026-10-11T19:00:00Z",
    "is_fallback": false
  }
}
```
*Примечание:* Поле `is_fallback: true` сигнализирует клиенту, что внешний шлюз котировок временно недоступен и отдаются резервные котировки из негативного кэша.

#### Список бюджетов с мультивалютным расчетом (GET /api/v1/budgets)

*Заголовки ответа:* `ETag: "9b64bc7a049d5fa9e1d8849bbfbc4a26"` (при совпадении с `If-None-Match` возвращается статус `304 Not Modified` с обязательным сохранением заголовка `ETag`).

*Правила условного кэширования и расчета:*
1. **Поддержка RFC 7232 / RFC 9110:** Сервер корректно обрабатывает точные ETag (`"hash"`), слабые валидаторы (`W/"hash"`), а также разделенные запятыми списки (`"tag1", "tag2"`).
2. **Валюта лимита:** `limit_amount`, `spent` и `remaining` — в валюте лимита `currency`. Траты в той же валюте учитываются по сумме чека, для лимита в базовой валюте — по `base_amount` (как раньше), остальные — по текущему кросс-курсу. Для лимитов в базовой валюте `spent` полностью согласован с `ETag`; у лимитов в другой валюте траты в иных валютах обновляются при следующей мутации данных (курсы в `ETag` не входят).
3. **Инвалидация ETag:** Хеш `ETag` автоматически инвалидируется при:
   - создании, обновлении или удалении бюджета (`POST /api/v1/budgets/`, `DELETE /api/v1/budgets/{id}`);
   - создании или удалении транзакций (`POST /api/v1/transactions/`, `DELETE /api/v1/transactions/{id}`);
   - пакетной синхронизации (`POST /api/v1/sync/`);
   - изменении базовой валюты пользователя в профиле (`PATCH /api/v1/auth/me`).

```json
{
  "status": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440002",
      "category": "food",
      "limit_amount": 500.00,
      "currency": "USD",
      "month": 10,
      "year": 2026,
      "spent": 250.00,
      "remaining": 250.00
    }
  ]
}
```

#### AI-отчёт за месяц (GET /api/v1/insights)
Параметры: `month`, `year` (по умолчанию текущие), `currency`, `locale`, `generate` (по умолчанию `true`; `false` — только сохранённый отчёт, без вызова LLM).

```json
{
  "status": "success",
  "data": {
    "insights": ["Совет 1", "Совет 2", "Совет 3"],
    "generated_at": "2026-10-15T09:30:00Z",
    "state": "ready",
    "is_outdated": false
  }
}
```

`state`:
* `ready` — отчёт есть (сгенерирован или сохранённый);
* `not_generated` — отчёта нет, а `generate=false` (`insights` пустой);
* `no_data` — будущий месяц или нет операций за период (`insights` пустой, LLM не вызывался);
* `quota_exceeded` — суточная квота исчерпана: сохранённого отчёта за период нет (`insights` — локализованное сообщение) или он устарел (`insights` — сохранённый отчёт, `is_outdated=true`).
* `busy` — LLM временно недоступен (ошибка/перегрузка провайдера); `insights` — локализованное сообщение для старых клиентов, ответ не кэшируется, квота не тратится.

`is_outdated` (bool, по умолчанию `false`) — отдан сохранённый отчёт, а операции или лимиты месяца изменились после его генерации (`generate=false` или исчерпана квота): суммы в тексте могут не совпадать с текущими.

#### Пакетная синхронизация (POST /api/v1/sync)

*Ограничения запроса:* Для защиты от перегрузок и исчерпания памяти пула списки `transactions`, `budgets`, `deleted_transaction_ids`, `deleted_budget_ids` и `deleted_budgets` ограничены лимитом **не более 500 элементов** на пакет (при превышении возвращается `422 Unprocessable Entity`).

*Идемпотентный ответ с синхронизированными и отклоненными сущностями:*

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
        "currency": "USD",
        "month": 10,
        "year": 2026,
        "spent": 100.00,
        "remaining": 400.00
      }
    ],
    "rejected": [
      {
        "id": "770e8400-e29b-41d4-a716-446655440099",
        "reason": "Идентификатор транзакции отклонен (конфликт идентификатора)"
      }
    ]
  }
}
```
*Примечание:* Поле `rejected` содержит элементы, которые не удалось синхронизировать из-за конфликта идентификатора (например, UUID уже принадлежит другой учетной записи). Такие записи исключаются клиентом из очереди повторных попыток без падения всего батча.

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

## Исключение: публичные HTML-страницы

`GET /privacy`, `GET /terms`, `GET /delete-account` не входят в API-контракт: они отдают `text/html` (не `ApiResponse`), доступны без авторизации и скрыты из OpenAPI. Единственный параметр — `lang` (`ru|en|uz|kk`, до 10 символов; иначе 422 в формате `ErrorResponse`).

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
