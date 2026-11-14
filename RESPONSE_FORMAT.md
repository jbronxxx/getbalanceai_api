# Стандартный формат API ответов

## Обзор

API использует единообразный формат ответов для всех эндпоинтов:

- **Успешные ответы**: `ApiResponse[T]` — обертка с данными результата (`data`)
- **Пагинированные ответы**: `ApiResponse[PaginatedResponse[T]]` — обертка со страницами списков (`items`, `total`, `limit`, `offset`)
- **Ошибочные ответы**: `ErrorResponse` — фиксированный контракт с машиночитаемым строковым кодом (`code`), понятным описанием (`message`) и опциональными деталями (`details`)
- **Простые ответы**: `BaseResponse` — для операций без значимых возвращаемых данных (например, logout, delete)

**Часовой пояс клиента.** Заголовок `X-Timezone-Offset` — смещение от UTC в минутах (UTC+5 → `300`, UTC-3 → `-180`). По нему считаются границы месяцев в `spent`/`remaining` бюджетов (`GET /budgets`, `POST /budgets`, `POST /sync`) и в AI-отчётах (`GET /insights`, в том числе месяц по умолчанию). Без заголовка или при значении вне `-720..840` — UTC, как раньше. ETag бюджетов зависит от смещения.

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

Вход и регистрация по email и паролю (`/auth/register`, `/auth/login`) выведены из API (`404`): аккаунты создаются только входом через Google или Apple.

#### Вход или регистрация через Google (POST /api/v1/auth/google)

**Тело запроса (`GoogleSignInRequest`):**
```json
{
  "id_token": "eyJhbGciOiJSUzI1NiIs...",
  "avatar_url": "https://lh3.googleusercontent.com/avatar.png",
  "name": "John Doe",
  "accept_legal": true
}
```

`accept_legal` обязателен и должен быть `true` (отметка «Принимаю условия» на экране входа, иначе `422`): вход записывает принятие текущих версий соглашения и политики. То же поле — в `POST /api/v1/auth/apple`.

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
      "created_at": "2026-09-10T10:30:00Z",
      "legal": {
        "terms_version": "2026-11-13",
        "terms_accepted": true,
        "privacy_version": "2026-11-13",
        "privacy_accepted": true
      }
    }
  }
}
```

#### Обновление профиля пользователя (PATCH /api/v1/auth/me)

Имя обрезается по краям; пустое имя или из одних пробелов — `422`.

```json
{
  "status": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "name": "John Doe",
    "avatar_url": "https://example.com/avatar.png",
    "base_currency": "EUR",
    "created_at": "2026-09-10T10:30:00Z",
    "legal": {
      "terms_version": "2026-11-13",
      "terms_accepted": true,
      "privacy_version": "2026-11-13",
      "privacy_accepted": true
    }
  }
}
```

#### Профиль с тарифом и лимитами (GET /api/v1/auth/me)

Тот же профиль плюс `subscription` и `limits`. Окна лимитов и `resets_at` — по часам клиента (заголовок `X-Timezone-Offset`, без него — UTC). Открыт и без принятой текущей версии соглашения.

```json
{
  "status": "success",
  "data": {
    "id": "550e8400-e29b-41d4-a716-446655440000",
    "email": "user@example.com",
    "name": "John Doe",
    "avatar_url": null,
    "base_currency": "EUR",
    "created_at": "2026-09-10T10:30:00Z",
    "legal": {"terms_version": "2026-11-15", "terms_accepted": true, "privacy_version": "2026-11-15", "privacy_accepted": true},
    "subscription": {"plan": "free", "expires_at": null, "will_renew": false, "is_trial": false},
    "limits": [
      {"feature": "ai_report", "period": "month", "limit": 1, "used": 0, "remaining": 1, "resets_at": "2026-11-30T19:00:00Z"},
      {"feature": "voice", "period": "day", "limit": 5, "used": 3, "remaining": 2, "resets_at": "2026-11-14T19:00:00Z"}
    ]
  }
}
```

* `subscription.plan` — `free` или `pro`. `expires_at` — у Pro дата продления (`will_renew=true`) или окончания доступа; null — Free без прошлой подписки или бессрочный Pro; у Free после окончания подписки — дата её окончания. `is_trial` — идёт пробный период.
* `limits` — по одной записи на функцию: `ai_report` (генерация ИИ-отчёта, `GET /insights` с `generate=true`) и `voice` (`POST /voice/parse`). Free — `ai_report` 1 в месяц, `voice` 5 в сутки; Pro — 5 и 30 в сутки. `used`/`remaining` — null, если счётчик временно недоступен (проверку сделает сервер при вызове).

#### Подписка (POST /api/v1/subscriptions/refresh)

Приложение вызывает после покупки и восстановления покупок: сервер запрашивает статус в RevenueCat (пользователь RevenueCat — id аккаунта) и сохраняет его, не дожидаясь вебхука. Требует принятой текущей версии соглашения; 10 запросов в минуту. Ответ — `subscription` из профиля:

```json
{"status": "success", "data": {"plan": "pro", "expires_at": "2026-12-14T10:00:00Z", "will_renew": true, "is_trial": true}}
```

`503 SERVICE_UNAVAILABLE` — RevenueCat не настроен или недоступен. Вебхук `POST /api/v1/subscriptions/webhooks/revenuecat` — только для RevenueCat (заголовок `Authorization` из настроек вебхука; неверный — `401 UNAUTHORIZED`, не настроен или сбой RevenueCat — `503`).

`legal` есть в каждом профиле (`/auth/google`, `/auth/apple`, `GET`/`PATCH /auth/me`, `/legal/accept`): текущие версии документов и приняты ли они. `terms_accepted: false` — приложение показывает экран новой версии соглашения (данные до принятия закрыты, `403 TERMS_NOT_ACCEPTED`); `privacy_accepted: false` — уведомление о новой политике, доступ не закрыт. `PATCH /auth/me` тоже требует принятой текущей версии соглашения.

#### Версии документов (GET /api/v1/legal)

Без авторизации. Язык пунктов `changes`: `?lang=` → `X-App-Language` → `Accept-Language` → `en`.

```json
{
  "status": "success",
  "data": {
    "terms": {
      "version": "2026-11-13",
      "effective_date": "2026-11-13",
      "changes": ["Условия нужно принять явно — отметкой на экране входа.", "…"]
    },
    "privacy": {
      "version": "2026-11-13",
      "effective_date": "2026-11-13",
      "changes": ["Мы храним дату и версию принятых вами документов.", "…"]
    }
  }
}
```

#### Принятие версий (POST /api/v1/legal/accept)

Доступен и без принятой текущей версии соглашения. Тело — показанные пользователю версии, хотя бы одна (`ГГГГ-ММ-ДД`):

```json
{ "terms_version": "2026-11-13", "privacy_version": "2026-11-13" }
```

Ответ — профиль (`ApiResponse[UserResponse]`) с обновлённым `legal`. Версия не текущая (вышла новая, пока экран был открыт) — `409 LEGAL_VERSION_OUTDATED`, `details: {"document": "terms", "current_version": "…"}`: запросить `GET /legal` заново. Лимит — 10 запросов в минуту.

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
1. **Валидация валюты (`currency`)**: разрешены только поддерживаемые ISO-коды (`USD`, `EUR`). Любой иной код отклоняется со статусом `422 Unprocessable Entity` (`code: VALIDATION_ERROR`).
2. **Значение по умолчанию**: если поле `currency` не передано или передано как `null`, сервер автоматически устанавливает базовую валюту профиля текущего пользователя (`users.base_currency`).
3. **Единое согласование сумм и курсов (`resolve_amounts`)**:
   - При совпадении валюты операции с базовой валютой пользователя: строго `exchange_rate = 1.0` и `base_amount = amount`. Любые переданные клиентом курсы или `base_amount` игнорируются для защиты от искажения финансовых данных.
   - При мультивалютной операции: клиентский `exchange_rate` принимается только при условии, что его отклонение от серверного кросс-курса не превышает лимит `CLIENT_RATE_TOLERANCE_PERCENT` (по умолчанию 15%). Иначе применяется серверный кросс-курс котировок.
   - Эквивалент в базовой валюте (`base_amount`) всегда рассчитывается сервером по формуле `amount * exchange_rate`, квантованной до 2 знаков (`0.01`). Клиентский `base_amount` используется сервером только для аудита и сверки.

#### Список транзакций с курсорной пагинацией (GET /api/v1/transactions)

*Заголовки ответа:* `ETag: "8f5a6b7c8d9e0f1a2b3c4d5e6f7a8b9c"` (отдается только на первой странице выборки без параметра `cursor`).

*Правила условного кэширования и пагинации:*
1. **Глобальный ETag коллекции:** ETag рассчитывается по всей коллекции транзакций пользователя `hash(user_id, count, max(updated_at), since)` и не зависит от размера страницы (`limit`). `updated_at` меняется при создании и любой правке транзакции (в том числе пересчёте `base_amount` при смене базовой валюты), количество — при удалении.
2. **Изоляция пагинации:** При переходе по курсору (`cursor != null`) заголовок `ETag` не выставляется, а заголовок `If-None-Match` игнорируется (сервер никогда не возвращает 304 для страниц пагинации).
3. **Ответ 304 Not Modified:** При повторном запросе первой страницы с валидным `If-None-Match` сервер возвращает HTTP 304 с обязательным сохранением заголовка `ETag`.

#### Котировки валют (GET /api/v1/currencies/rates)

*Заголовки ответа:* `Cache-Control: public, max-age=<CURRENCY_RATES_CACHE_TTL>` (сутки по умолчанию); для резервных котировок (`is_fallback=true`) — `max-age=<CURRENCY_RATES_FALLBACK_CACHE_TTL>` (180 с), чтобы клиент не держал их сутки.

```json
{
  "status": "success",
  "data": {
    "base": "USD",
    "rates": {
      "USD": 1.0,
      "EUR": 0.92
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
* `not_enough_data` — операций за период меньше 7 (`insights` пустой, LLM не вызывался, сохранённый отчёт не отдаётся; клиенты без поддержки состояния покажут «недостаточно данных»);
* `busy` — LLM временно недоступен (ошибка/перегрузка провайдера, ответ дольше `AI_REQUEST_TIMEOUT`) или недоступен счётчик лимита в Redis; `insights` — локализованное сообщение (или сохранённый отчёт с `is_outdated=true`), ответ не кэшируется, лимит не тратится.

Генерация (`generate=true`, а сохранённого актуального отчёта нет) расходует лимит тарифа `ai_report` — за любой месяц отчёта; лимит исчерпан — `403 PLAN_LIMIT_REACHED` (см. реестр ошибок), сохранённый отчёт (в том числе устаревший) — запросом с `generate=false`.

Параметры: `currency` — только `USD`, `EUR`; `locale` — код языка `ru` или с регионом `ru_RU` (используется язык; по умолчанию и для языка, которого нет, — `en`). Иначе `422 VALIDATION_ERROR`: оба значения попадают в промпт LLM и ключ кэша.

`is_outdated` (bool, по умолчанию `false`) — отдан сохранённый отчёт, а операции или лимиты месяца изменились после его генерации (`generate=false`): суммы в тексте могут не совпадать с текущими.

#### Голосовой ввод (POST /api/v1/voice/parse)
Тело: `text` — распознанная на устройстве фраза (1–500 символов, не пустая); `currency` — `USD` или `EUR`, валюта записи, если в фразе её нет; `locale` — как у AI-отчёта (по умолчанию `en`). «Сегодня» и «вчера» — по заголовку `X-Timezone-Offset`. Сервер ничего не сохраняет и не логирует текст.

```json
{
  "status": "success",
  "data": {
    "records": [
      {"type": "expense", "category": "food", "amount": "4.00", "currency": "EUR", "description": "Кофе", "date": "2026-11-11"},
      {"type": "expense", "category": "transport", "amount": "12.00", "currency": "EUR", "description": "Такси", "date": "2026-11-10"}
    ]
  }
}
```

* `records` — от 0 до 10 черновиков; пустой список — в фразе нет ни одной суммы. Категория всегда подходит к типу, `date` не позже сегодняшнего дня клиента, `description` (до 255 символов) может быть пустым.
* Ошибки: `403 PLAN_LIMIT_REACHED` — исчерпан суточный лимит тарифа `voice` (Free 5, Pro 30, сутки по часам клиента); `429 RATE_LIMIT_EXCEEDED` — больше 20 запросов в минуту; `503 SERVICE_UNAVAILABLE` — модель или Redis недоступны (лимит не тратится); `422 VALIDATION_ERROR` — пустой или длинный текст, другая валюта, некорректная локаль.

#### Пакетная синхронизация (POST /api/v1/sync)

*Ограничения запроса:* Для защиты от перегрузок и исчерпания памяти пула списки `transactions`, `budgets`, `deleted_transaction_ids`, `deleted_budget_ids`, `deleted_budgets`, `recurring_payments` и `deleted_recurring_payment_ids` ограничены лимитом **не более 500 элементов** на пакет (при превышении возвращается `422 Unprocessable Entity`).

*Границы значений:* `amount` и `limit_amount` — не больше `10^12` (иначе `422`, а в `/sync` — запись в `rejected` с кодом `INVALID_AMOUNT`); `description` транзакции длиннее 255 символов в `/sync` обрезается до 255 (в `POST /transactions` — `422`). Если сумма после пересчёта в базовую валюту не помещается в `Numeric(18, 2)`, `POST /transactions` возвращает `400 INVALID_TRANSACTION_AMOUNT`, а `/sync` помещает запись в `rejected`, не роняя батч.

*Идемпотентный ответ с синхронизированными и отклоненными сущностями:*

```json
{
  "status": "success",
  "data": {
    "synced_transactions": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "amount": 92.00,
        "currency": "EUR",
        "exchange_rate": 1.086957,
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
    "synced_recurring_payments": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440003",
        "type": "expense",
        "category": "housing",
        "amount": 350.00,
        "currency": "EUR",
        "description": "ЖКХ",
        "day_of_month": 15,
        "start_date": "2026-11-15",
        "end_date": null,
        "skipped_periods": ["2026-12"],
        "updated_at": "2026-11-10T12:00:00Z"
      }
    ],
    "rejected": [
      {
        "id": "770e8400-e29b-41d4-a716-446655440099",
        "code": "ID_CONFLICT",
        "reason": "Идентификатор транзакции отклонен (конфликт идентификатора)"
      }
    ]
  }
}
```
*Примечание:* Поле `rejected` содержит элементы, которые не удалось синхронизировать. `code` — машиночитаемая причина, по ней клиент показывает локализованный текст; `reason` — текст для журналов (на русском):
- `ID_CONFLICT` — UUID уже принадлежит другой учетной записи (транзакции, бюджеты, регулярные платежи);
- `INVALID_CURRENCY` — неподдерживаемый код валюты или сервер не смог определить курс валюты операции;
- `INVALID_TRANSACTION_AMOUNT` — сумма в базовой валюте не помещается в `Numeric(18, 2)`;
- `INVALID_CATEGORY` — категории нет на сервере или она не подходит к типу операции (доходная у расхода, лимит на доходную категорию);
- `INVALID_AMOUNT` — сумма, лимит, курс или сумма в базовой валюте вне допустимого диапазона (`≤ 0`, больше `10^12`);
- `INVALID_SYNC_ITEM` — запись не прошла проверку схемы по другой причине (тип операции, дата, формат `id`).

Каждая запись `transactions`, `budgets` и `recurring_payments` проверяется отдельно: запись, не прошедшая схему, попадает в `rejected`, остальные записи батча сохраняются (раньше — `422` на весь батч). `reason` содержит только поле и описание ошибки, без присланных значений. `422` на весь запрос остаётся для структуры батча: списки длиннее 500 элементов, некорректные `deleted_*`.

Для новой записи без клиентского `id` поле `id` в `rejected` равно `null`, поэтому клиенту следует присваивать UUID новым записям до отправки — это же делает повтор батча идемпотентным. Такие записи исключаются клиентом из очереди повторных попыток без падения всего батча.

*Регулярные платежи в `/sync`:* `recurring_payments` — платёж целиком (`id` создаёт клиент и он обязателен, `type`, `category` под тип, `amount` > 0, `currency`, `description` — может быть пустым, `day_of_month` 1–31, `start_date`, `end_date` не раньше начала или `null`, `skipped_periods` — до 120 месяцев `YYYY-MM`); повтор с тем же `id` заменяет платёж. `deleted_recurring_payment_ids` — удаление; чужие и несуществующие id игнорируются. Старые клиенты эти поля не передают и получают `synced_recurring_payments: []`.

#### Регулярные платежи (GET /api/v1/recurring-payments)

Весь список платежей и доходов пользователя по дню месяца, без пагинации. Заголовок `ETag`; с совпавшим `If-None-Match` — `304 Not Modified`. Платёж, которого нет в списке, удалён (клиент сверяет список с локальным).

```json
{
  "status": "success",
  "data": [
    {
      "id": "550e8400-e29b-41d4-a716-446655440003",
      "type": "expense",
      "category": "housing",
      "amount": 350.00,
      "currency": "EUR",
      "description": "ЖКХ",
      "day_of_month": 15,
      "start_date": "2026-11-15",
      "end_date": "2027-10-15",
      "skipped_periods": [],
      "updated_at": "2026-11-10T12:00:00Z"
    }
  ]
}
```

#### Изменения с прошлой синхронизации (GET /api/v1/sync/changes)

*Параметры:* `since` — `server_time` первой страницы прошлой синхронизации (ISO 8601; без него — все транзакции пользователя, первая синхронизация устройства); `cursor` — `next_cursor` предыдущей страницы; `limit` — размер страницы `1..500` (по умолчанию 200, вне диапазона — `422`). Повреждённый курсор — `400 INVALID_CURSOR`. Лимит — 120 запросов в минуту на пользователя.

*Семантика:* `transactions` — созданные или изменённые позже `since` (включая пересчёт при смене базовой валюты), по возрастанию `(updated_at, id)`; `deleted_transaction_ids` — удалённые позже `since`, только на первой странице (с курсором — пустой список). Окно `since` расширено сервером на 1 минуту назад, поэтому одна и та же запись может прийти в двух синхронизациях подряд — клиент применяет изменения идемпотентно. Клиент сохраняет `server_time` первой страницы и передаёт его как `since` после того, как применил все страницы. Транзакция, созданная заново с id удалённой, приходит в `transactions`, а не в `deleted_transaction_ids`.

```json
{
  "status": "success",
  "data": {
    "transactions": [
      {
        "id": "550e8400-e29b-41d4-a716-446655440001",
        "amount": 92.00,
        "currency": "EUR",
        "exchange_rate": 1.086957,
        "base_amount": 100.00,
        "description": "Ужин",
        "category": "food",
        "type": "expense",
        "date": "2026-10-06T12:00:00Z",
        "created_at": "2026-10-06T12:00:00Z"
      }
    ],
    "deleted_transaction_ids": ["770e8400-e29b-41d4-a716-446655440042"],
    "has_more": false,
    "next_cursor": null,
    "server_time": "2026-11-06T13:05:41.120394Z"
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
| `INVALID_CREDENTIALS` | 401 | Недействительный ID-токен Google или Apple |
| `USER_NOT_FOUND` | 401 / 404 | Пользователь не найден |
| `EXPIRED_TOKEN` | 401 | Истек срок действия JWT access или refresh токена |
| `INVALID_TOKEN` | 401 | Некорректная подпись, структура или тип JWT токена |
| `TOKEN_REVOKED` | 401 | Токен был отозван (например, после logout или ротации) |
| `TOKEN_NOT_FOUND` | 404 | Токен для отзыва при logout некорректен (нет `jti` или `exp`) |
| `TERMS_NOT_ACCEPTED` | 403 | Не принята текущая версия пользовательского соглашения: данные закрыты до `POST /legal/accept` (открыты `GET /auth/me`, выход, `DELETE /auth/me`) |
| `LEGAL_VERSION_OUTDATED` | 409 | `POST /legal/accept` с версией, которая уже не текущая; `details.current_version` — текущая |
| `TRANSACTION_NOT_FOUND` | 404 | Транзакция с указанным ID не найдена |
| `BUDGET_NOT_FOUND` | 404 | Бюджет с указанным ID/категорией не найден |
| `BUDGET_LIMIT_EXCEEDED` | 400 | Превышен установленный лимит бюджета по категории |
| `SYNC_FAILED` | 400 / 500 | Ошибка при выполнении пакетной офлайн-синхронизации |
| `INVALID_CURSOR` | 400 | Курсор пагинации `GET /transactions` повреждён (первая страница молча не возвращается) |
| `BAD_REQUEST` | 400 | Общая ошибка некорректного запроса |
| `UNAUTHORIZED` | 401 | Требуется авторизация |
| `FORBIDDEN` | 403 | Отсутствует токен доступа или доступ к ресурсу запрещен |
| `NOT_FOUND` | 404 | Запрашиваемый ресурс или URL не найден |
| `RATE_LIMIT_EXCEEDED` | 429 | Превышен лимит частоты запросов (Rate Limiting) |
| `PLAN_LIMIT_REACHED` | 403 | Исчерпан лимит функции на тарифе (`GET /insights` с генерацией, `POST /voice/parse`) — приложение показывает пейволл. `details`: `feature` (`ai_report`, `voice`), `plan` (`free`, `pro`), `limit`, `period` (`day`, `month`), `resets_at` (UTC) — начало следующего окна по часам клиента. Не путать с 429 `RATE_LIMIT_EXCEEDED` (частота запросов) |
| `INTERNAL_SERVER_ERROR` | 500 | Непредвиденная внутренняя ошибка сервера |
| `SERVICE_UNAVAILABLE` | 503 | Сервис, база данных или Redis временно недоступны; любой авторизованный запрос — нельзя проверить отзыв токена (сессия не завершена, запрос нужно повторить, refresh не нужен); при logout и `DELETE /auth/me` — не удалось записать отзыв токена (токен действует, запрос нужно повторить); при `DELETE /auth/me` — ещё и RevenueCat недоступен (покупатель не удалён, аккаунт цел, запрос нужно повторить). `/health` возвращает 503 с `details: {database, redis}` |


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
      "transactions.1.amount": "Input should be greater than 0",
      "limit_amount": "Input should be greater than 0"
    }
  }
}
```

Ключ `fields` — путь к полю без источника (`body`, `query`): для плоского тела это имя поля, для вложенных списков — с индексом записи (`transactions.1.amount`).

#### 2. Неверные учетные данные (401 Unauthorized)

```json
{
  "status": "error",
  "code": "INVALID_CREDENTIALS",
  "message": "Недействительный токен Google"
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

429 на `POST /auth/refresh` (30/мин по IP) не означает конец сессии: refresh-токен не отозван, обновление нужно повторить позже. Сессию завершает только 401.

Если ответ на `POST /auth/refresh` не дошёл (обрыв, таймаут), запрос можно повторить с тем же refresh-токеном в течение 30 секунд: сервер выдаст новую пару, а недошедшая станет недействительной. Новую пару клиент должен сохранять целиком (access и refresh вместе): позже 30 секунд прежний refresh-токен считается украденным, и сервер отзывает все сессии пользователя.

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

`GET /privacy`, `GET /terms`, `GET /delete-account` не входят в API-контракт: они отдают `text/html` (не `ApiResponse`), доступны без авторизации и скрыты из OpenAPI. Параметры: `lang` (`ru|en`, до 10 символов; иначе 422 в формате `ErrorResponse`) и у `/privacy`, `/terms` — `version` (`ГГГГ-ММ-ДД`, прежний текст из архива; неизвестная версия — 404, не дата — 422).

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
    message="Недействительный токен Google",
)

# Ошибка бизнес-правил
raise BadRequestException(
    code=ErrorCode.INVALID_TRANSACTION_AMOUNT,
    message="Сумма в базовой валюте слишком велика для сохранения",
)
```
