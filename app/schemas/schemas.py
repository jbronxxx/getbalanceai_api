"""Модуль Pydantic-схем для валидации входящих запросов и сериализации ответов (DTO)."""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    field_validator,
    model_validator,
)

from app.models.models import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    Category,
    CurrencyCode,
    TransactionType,
)

# ============================================================================
# Базовые response обертки для стандартизации ответов
# ============================================================================

T = TypeVar("T")


class ApiResponse(BaseModel, Generic[T]):
    """Универсальная обертка для успешных ответов API.

    Атрибуты:
        status: Статус ответа ('success').
        data: Данные результата (может быть любой тип T).
        message: Опциональное сообщение о результате.
    """

    status: str = Field(default="success", description="Статус ответа")
    data: T = Field(..., description="Данные результата")
    message: Optional[str] = Field(default=None, description="Опциональное сообщение о результате")


class PaginatedResponse(BaseModel, Generic[T]):
    """Универсальная обертка для пагинированных списков элементов (keyset/cursor pagination).

    Атрибуты:
        items: Список элементов текущей страницы.
        has_more: Флаг наличия следующих страниц.
        next_cursor: Курсор для получения следующей страницы.
        limit: Количество записей на странице.
    """

    items: List[T] = Field(..., description="Список элементов текущей страницы")
    has_more: bool = Field(..., description="Есть ли еще записи для загрузки")
    next_cursor: Optional[str] = Field(default=None, description="Курсор для получения следующей страницы")
    limit: int = Field(..., description="Лимит записей на странице")


class ErrorResponse(BaseModel):
    """Обертка для ошибочных ответов.

    Атрибуты:
        status: Статус ответа ('error').
        code: Уникальный строковый код ошибки (например, 'BUDGET_LIMIT_EXCEEDED').
        message: Понятное описание ошибки для пользователя.
        details: Опциональные структурированные детали ошибки.
    """

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(default="error", description="Статус ответа")
    code: str = Field(..., description="Машиночитаемый строковый код ошибки")
    message: str = Field(..., description="Понятное описание ошибки")
    details: Optional[dict] = Field(default=None, description="Дополнительные детали ошибки")


class BaseResponse(BaseModel):
    """Базовая схема ответа для простых операций (без данных).

    Используется для операций которые не возвращают значимые данные
    (например logout, delete).
    """

    model_config = ConfigDict(from_attributes=True)

    status: str = Field(default="success", description="Статус ответа")
    message: Optional[str] = Field(default=None, description="Сообщение о результате операции")


# ============================================================================
# Схемы аутентификации и пользователей
# ============================================================================


class UserBase(BaseModel):
    """Базовая схема пользователя с общими полями.

    Атрибуты:
        user_id (uuid.UUID): Уникальный идентификатор пользователя.
    """

    user_id: uuid.UUID = Field(..., description="ID пользователя")


class UserRegister(BaseModel):
    """Схема запроса на регистрацию нового пользователя.

    Атрибуты:
        email (EmailStr): Адрес электронной почты пользователя.
        password (str): Пароль пользователя (от 6 до 72 символов, минимум 1 буква и 1 цифра).
        name (str): Имя пользователя.
    """

    email: EmailStr = Field(..., description="Email адрес пользователя")
    password: str = Field(
        ...,
        min_length=6,
        description="Пароль пользователя (от 6 символов, до 72 байт, минимум 1 буква и 1 цифра)",
    )
    name: str = Field(..., min_length=1, max_length=100, description="Имя пользователя")

    @field_validator("password")
    @classmethod
    def validate_password_complexity(cls, v: str) -> str:
        """Проверяет пароль на наличие букв и цифр, а также ограничение длины bcrypt (72 байта)."""
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Длина пароля не должна превышать 72 байта")
        has_letter = any(c.isalpha() for c in v)
        has_digit = any(c.isdigit() for c in v)
        if not (has_letter and has_digit):
            raise ValueError("Пароль должен содержать как минимум одну букву и одну цифру")
        return v


class UserLogin(BaseModel):
    """Схема запроса для аутентификации пользователя (логин).

    Атрибуты:
        email (EmailStr): Зарегистрированный адрес электронной почты.
        password (str): Пароль пользователя (до 72 символов).
    """

    email: EmailStr = Field(..., description="Email адрес пользователя")
    password: str = Field(..., description="Пароль пользователя")

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, v: str) -> str:
        """Проверяет пароль на ограничение длины bcrypt (72 байта)."""
        if len(v.encode("utf-8")) > 72:
            raise ValueError("Длина пароля не должна превышать 72 байта")
        return v


class GoogleSignInRequest(BaseModel):
    """Схема запроса для аутентификации через Google."""

    id_token: str = Field(..., description="Токен идентификации от Google")
    avatar_url: Optional[str] = Field(
        default=None, max_length=500, description="URL аватара от клиента (резервный источник при кэшировании токена)"
    )
    name: Optional[str] = Field(
        default=None, max_length=100, description="Имя пользователя от клиента (резервный источник при кэшировании токена)"
    )


class AppleSignInRequest(BaseModel):
    """Схема запроса для аутентификации через Apple."""

    identity_token: str = Field(..., description="Токен идентификации от Apple")


class RefreshTokenRequest(BaseModel):
    """Схема запроса на обновление access-токена с использованием refresh-токена.

    Атрибуты:
        refresh_token (str): Действительный JWT refresh-токен.
    """

    refresh_token: str = Field(..., description="JWT refresh токен для обновления access токена")


class UserResponse(BaseModel):
    """Схема ответа с публичными данными пользователя.

    Атрибуты:
        id (uuid.UUID): Уникальный ID пользователя.
        email (str): Email пользователя.
        name (str): Имя пользователя.
        avatar_url (str | None): URL аватара пользователя.
        base_currency (str): Основная валюта учета пользователя.
        created_at (datetime): Дата регистрации аккаунта.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="ID пользователя")
    email: str = Field(..., description="Email пользователя")
    name: str = Field(..., description="Имя пользователя")
    avatar_url: Optional[str] = Field(default=None, description="URL аватара пользователя")
    base_currency: str = Field(default=CurrencyCode.USD.value, description="Основная валюта учета пользователя")
    created_at: datetime = Field(..., description="Дата и время создания аккаунта")


class TokenResponse(BaseModel):
    """Схема ответа с JWT-токенами доступа и обновления.

    Атрибуты:
        access_token (str): Сгенерированный JWT-токен доступа.
        refresh_token (str): JWT-токен для обновления access-токена.
        token_type (str): Тип токена (по умолчанию "bearer").
        user (Optional[UserResponse]): Данные профиля пользователя.
    """

    access_token: str = Field(..., description="JWT токен доступа")
    refresh_token: str = Field(..., description="JWT токен для обновления access токена")
    token_type: str = Field(default="bearer", description="Тип токена авторизации")
    user: Optional[UserResponse] = Field(default=None, description="Данные профиля пользователя")


class UserUpdateRequest(BaseModel):
    """Схема запроса на частичное обновление профиля пользователя.

    Атрибуты:
        name (str | None): Новое имя пользователя.
        avatar_url (str | None): URL нового аватара.
        base_currency (str | None): Базовая валюта учета (USD, EUR, KZT, UZS).
    """

    name: Optional[str] = Field(default=None, max_length=100, description="Имя пользователя")
    avatar_url: Optional[str] = Field(default=None, max_length=500, description="URL аватара")
    base_currency: Optional[str] = Field(default=None, max_length=10, description="Основная валюта учета")

    @field_validator("base_currency")
    @classmethod
    def validate_base_currency(cls, v: Optional[str]) -> Optional[str]:
        """Проверить, что код валюты входит в список разрешенных."""
        if v is not None:
            v = v.upper().strip()
            if v not in SUPPORTED_CURRENCIES:
                raise ValueError(f"Валюта {v} не поддерживается. Разрешены: {set(SUPPORTED_CURRENCIES)}")
        return v


# ============================================================================
# Схемы финансовых транзакций
# ============================================================================


class TransactionCreate(BaseModel):
    """Схема запроса на создание транзакции (дохода или расхода).

    Атрибуты:
        amount (Decimal): Сумма операции (> 0).
        currency (str): Код валюты операции (USD, UZS, EUR и др.).
        exchange_rate (Decimal): Курс к базовой валюте.
        base_amount (Decimal | None): Сумма в базовой валюте.
        description (str): Описание операции.
        category (Category): Категория транзакции.
        type (TransactionType): Тип транзакции (income / expense).
        date (datetime | None): Дата и время операции.
    """

    amount: Decimal = Field(..., gt=Decimal("0"), description="Сумма операции (должна быть больше 0)")
    currency: Optional[str] = Field(
        default=None,
        description="Код валюты операции (USD, EUR, KZT, UZS). По умолчанию базовая валюта пользователя",
    )
    exchange_rate: Optional[Decimal] = Field(
        default=None,
        gt=Decimal("0"),
        le=Decimal("1000000000"),
        description="Курс валюты к базовой (опционально, рассчитывается сервером)",
    )
    base_amount: Optional[Decimal] = Field(
        default=None,
        gt=Decimal("0"),
        description="Сумма в базовой валюте (опционально, рассчитывается сервером)",
    )
    description: str = Field(..., min_length=1, max_length=255, description="Описание или назначение платежа")
    category: Category = Field(..., description="Категория операции")
    type: TransactionType = Field(..., description="Тип операции: income (доход) или expense (расход)")
    date: datetime | None = Field(default=None, description="Дата транзакции (по умолчанию текущая)")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> Optional[str]:
        """Нормализовать код валюты к верхнему регистру и валидировать по списку поддерживаемых."""
        if v is None:
            return None
        if isinstance(v, str):
            code = v.strip().upper()
            if not code:
                return None
            if code not in SUPPORTED_CURRENCIES:
                raise ValueError(f"Валюта '{code}' не поддерживается. Разрешены: {sorted(SUPPORTED_CURRENCIES)}")
            return code
        if isinstance(v, CurrencyCode):
            return v.value
        raise ValueError(f"Некорректный формат кода валюты: {v}")

    @model_validator(mode="after")
    def validate_category_type_compatibility(self) -> "TransactionCreate":
        """Проверить совместимость выбранной категории с типом транзакции."""
        if self.type == TransactionType.income and self.category not in INCOME_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in INCOME_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для доходных операций. " f"Допустимые категории: {allowed}."
            )
        if self.type == TransactionType.expense and self.category not in EXPENSE_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для расходных операций. " f"Допустимые категории: {allowed}."
            )
        return self


class TransactionResponse(BaseModel):
    """Схема ответа с данными сохраненной транзакции.

    Атрибуты:
        id (uuid.UUID): Уникальный идентификатор транзакции.
        amount (Decimal): Сумма операции.
        currency (str): Код валюты операции.
        exchange_rate (Decimal): Курс к базовой валюте.
        base_amount (Decimal): Сумма в базовой валюте.
        description (str): Описание транзакции.
        category (Category): Категория.
        type (TransactionType): Тип (income / expense).
        date (datetime): Дата и время транзакции.
        created_at (datetime): Дата создания записи в БД.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="ID транзакции")
    amount: Decimal = Field(..., description="Сумма транзакции")
    currency: str = Field(default="USD", description="Код валюты операции")
    exchange_rate: Decimal = Field(default=Decimal("1.0"), description="Курс валюты к базовой валюте")
    base_amount: Decimal = Field(..., description="Сумма в базовой валюте")
    description: str = Field(..., description="Описание транзакции")
    category: Category = Field(..., description="Категория транзакции")
    type: TransactionType = Field(..., description="Тип операции")
    date: datetime = Field(..., description="Дата транзакции")
    created_at: datetime = Field(..., description="Дата добавления в базу")


# ============================================================================
# Схемы бюджетов
# ============================================================================


def _normalize_budget_currency(v: Any) -> Optional[str]:
    """Код валюты лимита в верхнем регистре; None — валюта не передана (старый клиент)."""
    if v is None:
        return None
    if isinstance(v, CurrencyCode):
        return v.value
    if isinstance(v, str):
        code = v.strip().upper()
        if not code:
            return None
        if code not in SUPPORTED_CURRENCIES:
            raise ValueError(f"Валюта '{code}' не поддерживается. Разрешены: {sorted(SUPPORTED_CURRENCIES)}")
        return code
    raise ValueError(f"Некорректный формат кода валюты: {v}")


class BudgetCreate(BaseModel):
    """Схема запроса на установку лимита бюджета по категории.

    Атрибуты:
        category (Category): Категория расходов.
        limit_amount (Decimal): Устанавливаемый лимит суммы в валюте лимита.
        currency (str | None): Валюта лимита; по умолчанию базовая валюта пользователя.
        month (int): Месяц (1-12).
        year (int): Год.
    """

    category: Category = Field(..., description="Категория расходов")
    limit_amount: Decimal = Field(..., gt=Decimal("0"), description="Лимит суммы на указанный месяц")
    currency: Optional[str] = Field(
        default=None,
        description="Валюта лимита (USD, EUR, KZT, UZS). По умолчанию базовая валюта пользователя",
    )
    month: int = Field(..., ge=1, le=12, description="Номер месяца (от 1 до 12)")
    year: int = Field(..., ge=2000, le=2100, description="Год")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> Optional[str]:
        """Нормализовать код валюты лимита к верхнему регистру и валидировать по списку поддерживаемых."""
        return _normalize_budget_currency(v)

    @model_validator(mode="after")
    def validate_expense_category(self) -> "BudgetCreate":
        """Проверить, что категория относится к допустимым категориям расходов."""
        if self.category not in EXPENSE_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для бюджета. "
                f"Бюджет может быть установлен только для расходных категорий: {allowed}."
            )
        return self


class BudgetResponse(BaseModel):
    """Схема ответа с информацией о бюджете и расчетом расходов.

    Атрибуты:
        id (uuid.UUID): Идентификатор записи бюджета.
        category (Category): Категория расходов.
        limit_amount (Decimal): Установленный лимит бюджета в валюте лимита.
        currency (str): Валюта лимита; spent и remaining — в ней же.
        month (int): Месяц действия.
        year (int): Год действия.
        spent (Decimal): Фактически потраченная сумма за указанный период.
        remaining (Decimal): Оставшаяся доступная сумма лимита.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="ID бюджета")
    category: Category = Field(..., description="Категория расходов")
    limit_amount: Decimal = Field(..., description="Установленный лимит бюджета в валюте лимита")
    currency: str = Field(..., description="Валюта лимита (USD, EUR, KZT, UZS)")
    month: int = Field(..., description="Месяц действия бюджета (1-12)")
    year: int = Field(..., description="Год действия бюджета")
    spent: Decimal = Field(
        default=Decimal("0.00"),
        description=(
            "Фактически израсходованная сумма в валюте лимита: операции в этой же валюте — по сумме чека, "
            "в базовой — по base_amount, в остальных — по текущему кросс-курсу"
        ),
    )
    remaining: Decimal = Field(
        default=Decimal("0.00"),
        description="Остаток лимита в валюте лимита",
    )


# ============================================================================
# Схемы синхронизации данных оффлайн пользователя с сервером
# ============================================================================


class TransactionSyncItem(BaseModel):
    """Схема отдельной транзакции при пакетной синхронизации."""

    id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID транзакции на сервере (если транзакция уже существует)",
    )
    local_id: Optional[int] = Field(default=None, description="Локальный ID на клиенте")
    type: TransactionType = Field(..., description="Тип операции")
    category: Category = Field(..., description="Категория")
    amount: Decimal = Field(..., gt=Decimal("0"), description="Сумма")
    currency: Optional[str] = Field(
        default=None,
        description="Код валюты операции (USD, EUR, KZT, UZS). По умолчанию базовая валюта пользователя",
    )
    exchange_rate: Optional[Decimal] = Field(
        default=None,
        gt=Decimal("0"),
        le=Decimal("1000000000"),
        description="Курс валюты к базовой (опционально, рассчитывается сервером)",
    )
    base_amount: Optional[Decimal] = Field(
        default=None,
        gt=Decimal("0"),
        description="Сумма в базовой валюте (опционально, рассчитывается сервером)",
    )
    date: datetime = Field(..., description="Дата и время")
    description: str = Field(..., description="Описание")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> Optional[str]:
        """Нормализовать код валюты к верхнему регистру и валидировать по списку поддерживаемых."""
        if v is None:
            return None
        if isinstance(v, str):
            code = v.strip().upper()
            if not code:
                return None
            if code not in SUPPORTED_CURRENCIES:
                raise ValueError(f"Валюта '{code}' не поддерживается. Разрешены: {sorted(SUPPORTED_CURRENCIES)}")
            return code
        if isinstance(v, CurrencyCode):
            return v.value
        raise ValueError(f"Некорректный формат кода валюты: {v}")

    @model_validator(mode="after")
    def validate_category_type_compatibility(self) -> "TransactionSyncItem":
        """Проверить совместимость выбранной категории с типом транзакции."""
        if self.type == TransactionType.income and self.category not in INCOME_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in INCOME_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для доходных операций. " f"Допустимые категории: {allowed}."
            )
        if self.type == TransactionType.expense and self.category not in EXPENSE_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для расходных операций. " f"Допустимые категории: {allowed}."
            )
        return self


class BudgetSyncItem(BaseModel):
    """Схема отдельного бюджета при пакетной синхронизации."""

    id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID бюджета (для обновления существующего бюджета или сохранения оффлайн-идентификатора)",
    )
    category: Category = Field(..., description="Расходная категория")
    limit_amount: Decimal = Field(
        default=Decimal("0.00"),
        ge=Decimal("0"),
        description="Лимит бюджета в валюте лимита",
    )
    currency: Optional[str] = Field(
        default=None,
        description=(
            "Валюта лимита (USD, EUR, KZT, UZS). Если не передана: у существующего лимита сохраняется, "
            "новый создаётся в базовой валюте пользователя"
        ),
    )
    month: int = Field(..., ge=1, le=12, description="Месяц действия бюджета (1-12)")
    year: int = Field(..., ge=2000, le=2100, description="Год действия бюджета")
    is_deleted: bool = Field(default=False, description="Флаг удаления бюджета на клиенте")
    deleted: Optional[bool] = Field(default=None, description="Альтернативный флаг удаления бюджета")

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> Optional[str]:
        """Нормализовать код валюты лимита к верхнему регистру и валидировать по списку поддерживаемых."""
        return _normalize_budget_currency(v)

    @model_validator(mode="after")
    def validate_expense_category(self) -> "BudgetSyncItem":
        """Проверить, что категория относится к допустимым категориям расходов."""
        if self.category not in EXPENSE_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
            raise ValueError(
                f"Категория '{self.category.value}' недопустима для бюджета. "
                f"Бюджет может быть установлен только для расходных категорий: {allowed}."
            )
        return self

    @property
    def check_deleted(self) -> bool:
        """Проверить флаг удаления."""
        if self.deleted is not None:
            return self.deleted
        return self.is_deleted


class SyncPayload(BaseModel):
    """Схема пакета данных для синхронизации."""

    transactions: List[TransactionSyncItem] = Field(
        default_factory=list,
        max_length=500,
        description="Список транзакций (максимум 500 элементов)",
    )
    budgets: List[BudgetSyncItem] = Field(
        default_factory=list,
        max_length=500,
        description="Список бюджетов (максимум 500 элементов)",
    )
    deleted_budget_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=500,
        description="Список ID бюджетов для удаления (максимум 500 элементов)",
    )
    deleted_transaction_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=500,
        description="Список ID транзакций для удаления (максимум 500 элементов)",
    )
    deleted_budgets: List[BudgetSyncItem] = Field(
        default_factory=list,
        max_length=500,
        description="Список объектов бюджетов для удаления по категории и периоду (максимум 500 элементов)",
    )


class SyncRejectedItem(BaseModel):
    """Схема элемента, отклоненного при пакетной синхронизации."""

    id: Optional[uuid.UUID] = Field(default=None, description="Идентификатор отклоненного элемента")
    reason: str = Field(..., description="Причина отклонения элемента")


class SyncResponse(BaseModel):
    """Схема ответа на синхронизацию."""

    synced_transactions: List[TransactionResponse] = Field(..., description="Синхронизированные транзакции")
    synced_budgets: List[BudgetResponse] = Field(..., description="Синхронизированные бюджеты")
    rejected: List[SyncRejectedItem] = Field(
        default_factory=list,
        description="Список отклоненных элементов (конфликты ID или чужие записи)",
    )


# ============================================================================
# Схемы AI-инсайтов
# ============================================================================


class InsightState(str, enum.Enum):
    """Состояние AI-отчёта за период."""

    ready = "ready"  # Отчёт есть (сгенерирован сейчас или взят из кэша)
    not_generated = "not_generated"  # Отчёта нет, генерация не запрашивалась (generate=false)
    no_data = "no_data"  # Анализировать нечего: будущий месяц или нет операций за период
    # Суточная квота генераций исчерпана: отчёта за период нет (insights — сообщение о лимите)
    # или он устарел (insights — сохранённый отчёт, is_outdated=true)
    quota_exceeded = "quota_exceeded"
    busy = "busy"  # LLM временно недоступен; insights — вежливое сообщение, кэшировать нельзя


class InsightResponse(BaseModel):
    """Схема ответа с персональными рекомендациями AI.

    Атрибуты:
        insights (list[str]): Список текстовых рекомендаций (при quota_exceeded — сообщение о лимите
            или устаревший сохранённый отчёт).
        generated_at (datetime): Временная метка генерации ответа.
        state (InsightState): Состояние отчёта за период.
        is_outdated (bool): Отчёт сохранённый, а данные месяца изменились после его генерации.
    """

    insights: list[str] = Field(..., description="Список аналитических советов и выводов")
    generated_at: datetime = Field(..., description="Дата и время формирования аналитики")
    state: InsightState = Field(InsightState.ready, description="Состояние отчёта за период")
    is_outdated: bool = Field(
        False,
        description=(
            "Сохранённый отчёт, а операции или лимиты месяца изменились после генерации: суммы в тексте "
            "могут не совпадать с текущими (generate=false или исчерпана суточная квота)"
        ),
    )


# ============================================================================
# Схемы котировок валют
# ============================================================================


class CurrencyRatesResponse(BaseModel):
    """Схема ответа со справочником актуальных курсов валют."""

    base: str = Field(default="USD", description="Базовая валюта котировок")
    rates: dict[str, Decimal] = Field(..., description="Словарь курсов валют к базовой (USD, EUR, UZS, KZT)")
    updated_at: datetime = Field(..., description="Дата и время актуализации котировок")
    is_fallback: bool = Field(
        default=False,
        description="Признак резервных котировок при недоступности внешнего шлюза",
    )
