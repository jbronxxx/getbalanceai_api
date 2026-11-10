"""Модуль Pydantic-схем для валидации входящих запросов и сериализации ответов (DTO)."""

import enum
import re
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Generic, List, Optional, TypeVar

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from app.exceptions import ErrorCode
from app.models.models import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    Category,
    CurrencyCode,
    TransactionType,
)

# Верхняя граница сумм операций и лимитов: с запасом под колонку Numeric(18, 2) и пересчёт в базовую валюту
MAX_MONEY_AMOUNT = Decimal("1000000000000")
# Длина описания операции — как у колонки transactions.description
DESCRIPTION_MAX_LENGTH = 255

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

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """Имя без пробелов по краям; пустое имя не сохраняется — в профиле осталось бы пустое место."""
        if v is not None:
            v = v.strip()
            if not v:
                raise ValueError("Имя не может быть пустым")
        return v

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

    amount: Decimal = Field(
        ..., gt=Decimal("0"), le=MAX_MONEY_AMOUNT, description="Сумма операции (больше 0, не больше 10^12)"
    )
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
    description: str = Field(
        ..., min_length=1, max_length=DESCRIPTION_MAX_LENGTH, description="Описание или назначение платежа"
    )
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


def _normalize_currency_code(v: Any) -> Optional[str]:
    """Код валюты в верхнем регистре; None — валюта не передана (например, старым клиентом)."""
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
    limit_amount: Decimal = Field(
        ..., gt=Decimal("0"), le=MAX_MONEY_AMOUNT, description="Лимит суммы на указанный месяц (не больше 10^12)"
    )
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
        return _normalize_currency_code(v)

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


def _ensure_category_fits_type(category: Category, tx_type: TransactionType) -> None:
    """Категория должна подходить к типу операции; иначе — ошибка типа invalid_category (код INVALID_CATEGORY в /sync)."""
    if tx_type == TransactionType.income and category not in INCOME_CATEGORIES:
        allowed = ", ".join(sorted(c.value for c in INCOME_CATEGORIES))
        raise PydanticCustomError(
            "invalid_category",
            f"Категория '{category.value}' недопустима для доходных операций. Допустимые категории: {allowed}.",
        )
    if tx_type == TransactionType.expense and category not in EXPENSE_CATEGORIES:
        allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
        raise PydanticCustomError(
            "invalid_category",
            f"Категория '{category.value}' недопустима для расходных операций. Допустимые категории: {allowed}.",
        )


def _truncate_description(v: Any) -> Any:
    """Обрезать описание до длины колонки БД.

    Оффлайн-очередь клиента отправляет один и тот же батч, пока он не примется: отказ из-за
    длинного описания блокировал бы синхронизацию всех остальных записей пользователя.
    """
    if isinstance(v, str) and len(v) > DESCRIPTION_MAX_LENGTH:
        return v[:DESCRIPTION_MAX_LENGTH]
    return v


class TransactionSyncItem(BaseModel):
    """Схема отдельной транзакции при пакетной синхронизации."""

    id: Optional[uuid.UUID] = Field(
        default=None,
        description="ID транзакции на сервере (если транзакция уже существует)",
    )
    local_id: Optional[int] = Field(default=None, description="Локальный ID на клиенте")
    type: TransactionType = Field(..., description="Тип операции")
    category: Category = Field(..., description="Категория")
    amount: Decimal = Field(..., gt=Decimal("0"), le=MAX_MONEY_AMOUNT, description="Сумма (не больше 10^12)")
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
    description: str = Field(..., description="Описание (длиннее 255 символов обрезается)")

    @field_validator("description", mode="before")
    @classmethod
    def truncate_description(cls, v: Any) -> Any:
        """Обрезать описание до длины колонки БД (см. _truncate_description)."""
        return _truncate_description(v)

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
        """Проверить совместимость выбранной категории с типом транзакции (тип ошибки invalid_category)."""
        _ensure_category_fits_type(self.category, self.type)
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
        le=MAX_MONEY_AMOUNT,
        description="Лимит бюджета в валюте лимита (не больше 10^12)",
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
        return _normalize_currency_code(v)

    @model_validator(mode="after")
    def validate_expense_category(self) -> "BudgetSyncItem":
        """Проверить, что категория относится к допустимым категориям расходов (тип ошибки invalid_category)."""
        if self.category not in EXPENSE_CATEGORIES:
            allowed = ", ".join(sorted(c.value for c in EXPENSE_CATEGORIES))
            raise PydanticCustomError(
                "invalid_category",
                f"Категория '{self.category.value}' недопустима для бюджета. "
                f"Бюджет может быть установлен только для расходных категорий: {allowed}.",
            )
        return self

    @property
    def check_deleted(self) -> bool:
        """Проверить флаг удаления."""
        if self.deleted is not None:
            return self.deleted
        return self.is_deleted


# Месяц в списке пропусков регулярного платежа: «2026-11»
PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
# Пропусков у платежа не больше, чем месяцев в 10 годах
RECURRING_SKIPPED_MAX = 120


class RecurringPaymentSyncItem(BaseModel):
    """Регулярный платёж или доход в пакетной синхронизации: создание или полная замена по id."""

    id: uuid.UUID = Field(..., description="ID платежа (создаётся на клиенте)")
    type: TransactionType = Field(..., description="Расход или доход")
    category: Category = Field(..., description="Категория будущих записей")
    amount: Decimal = Field(
        ..., gt=Decimal("0"), le=MAX_MONEY_AMOUNT, description="Ожидаемая сумма в валюте платежа (не больше 10^12)"
    )
    currency: str = Field(..., description="Валюта платежа (USD, EUR, KZT, UZS)")
    description: str = Field(default="", description="Название платежа (длиннее 255 символов обрезается)")
    day_of_month: int = Field(..., ge=1, le=31, description="День месяца; в коротком месяце — его последний день")
    start_date: date = Field(..., description="Первый день платежа")
    end_date: Optional[date] = Field(default=None, description="Последний день платежа; null — без срока")
    skipped_periods: List[str] = Field(
        default_factory=list,
        max_length=RECURRING_SKIPPED_MAX,
        description="Пропущенные месяцы в формате YYYY-MM",
    )

    @field_validator("description", mode="before")
    @classmethod
    def truncate_description(cls, v: Any) -> Any:
        """Обрезать описание до длины колонки БД (см. _truncate_description)."""
        return _truncate_description(v)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, v: Any) -> Optional[str]:
        """Нормализовать код валюты к верхнему регистру и валидировать по списку поддерживаемых."""
        return _normalize_currency_code(v)

    @field_validator("skipped_periods")
    @classmethod
    def validate_skipped_periods(cls, v: List[str]) -> List[str]:
        """Месяцы в формате YYYY-MM, без повторов, по возрастанию."""
        pattern = re.compile(PERIOD_PATTERN)
        for period in v:
            if not pattern.match(period):
                raise ValueError(f"Месяц '{period}' должен быть в формате YYYY-MM")
        return sorted(set(v))

    @model_validator(mode="after")
    def validate_schedule(self) -> "RecurringPaymentSyncItem":
        """Категория подходит к типу; срок не раньше начала."""
        _ensure_category_fits_type(self.category, self.type)
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("Дата окончания не может быть раньше даты начала")
        return self


class RecurringPaymentResponse(BaseModel):
    """Регулярный платёж или доход в ответе сервера."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(..., description="ID платежа")
    type: TransactionType = Field(..., description="Расход или доход")
    category: Category = Field(..., description="Категория будущих записей")
    amount: Decimal = Field(..., description="Ожидаемая сумма в валюте платежа")
    currency: str = Field(..., description="Валюта платежа")
    description: str = Field(..., description="Название платежа")
    day_of_month: int = Field(..., description="День месяца (1-31)")
    start_date: date = Field(..., description="Первый день платежа")
    end_date: Optional[date] = Field(default=None, description="Последний день платежа; null — без срока")
    skipped_periods: List[str] = Field(default_factory=list, description="Пропущенные месяцы YYYY-MM")
    updated_at: datetime = Field(..., description="Время последнего изменения")


class SyncRejectedItem(BaseModel):
    """Схема элемента, отклоненного при пакетной синхронизации."""

    id: Optional[uuid.UUID] = Field(default=None, description="Идентификатор отклоненного элемента")
    code: str = Field(
        ...,
        description=(
            "Машиночитаемый код причины: ID_CONFLICT, INVALID_CURRENCY, INVALID_TRANSACTION_AMOUNT, "
            "INVALID_CATEGORY, INVALID_AMOUNT, INVALID_SYNC_ITEM"
        ),
    )
    reason: str = Field(..., description="Причина отклонения элемента")


# Поля сумм: ошибка в них — код INVALID_AMOUNT
_SYNC_AMOUNT_FIELDS = {"amount", "limit_amount", "base_amount", "exchange_rate"}
# Сколько записей в списке проверяется по одной; длиннее — 422 на весь запрос (max_length)
SYNC_LIST_MAX_LENGTH = 500


def _invalid_sync_item(raw: Any, error: ValidationError, path: str) -> SyncRejectedItem:
    """Запись батча, не прошедшая схему, — в rejected с кодом причины.

    Текст причины — номер записи (path: transactions.1), поле и описание ошибки, без присланных значений (суммы и описания —
    персональные финансовые данные).
    """
    item_id = None
    if isinstance(raw, dict):
        try:
            item_id = uuid.UUID(str(raw.get("id")))
        except ValueError:
            item_id = None

    errors = error.errors(include_input=False, include_url=False)
    # Отсутствующее поле — ошибка формата записи (INVALID_SYNC_ITEM), а не неверное значение
    fields = {str(err["loc"][0]) for err in errors if err["loc"] and err["type"] != "missing"}
    if any(err["type"] == "invalid_category" for err in errors) or "category" in fields:
        code = ErrorCode.INVALID_CATEGORY
    elif "currency" in fields:
        code = ErrorCode.INVALID_CURRENCY
    elif fields & _SYNC_AMOUNT_FIELDS:
        code = ErrorCode.INVALID_AMOUNT
    else:
        code = ErrorCode.INVALID_SYNC_ITEM
    first = errors[0]
    field = ".".join([path, *(str(part) for part in first["loc"])])
    reason = f"{field}: {first['msg']}"
    return SyncRejectedItem(id=item_id, code=code, reason=reason)


class SyncPayload(BaseModel):
    """Схема пакета данных для синхронизации."""

    transactions: List[TransactionSyncItem] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Список транзакций (максимум 500 элементов)",
    )
    budgets: List[BudgetSyncItem] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Список бюджетов (максимум 500 элементов)",
    )
    deleted_budget_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Список ID бюджетов для удаления (максимум 500 элементов)",
    )
    deleted_transaction_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Список ID транзакций для удаления (максимум 500 элементов)",
    )
    deleted_budgets: List[BudgetSyncItem] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Список объектов бюджетов для удаления по категории и периоду (максимум 500 элементов)",
    )
    recurring_payments: List[RecurringPaymentSyncItem] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="Регулярные платежи и доходы: создание или замена по id (максимум 500 элементов)",
    )
    deleted_recurring_payment_ids: List[uuid.UUID] = Field(
        default_factory=list,
        max_length=SYNC_LIST_MAX_LENGTH,
        description="ID регулярных платежей для удаления (максимум 500 элементов)",
    )
    # Заполняется валидатором: записи transactions, budgets и recurring_payments, не прошедшие схему.
    # Клиент это поле не передаёт
    invalid_items: SkipJsonSchema[List[SyncRejectedItem]] = Field(default_factory=list, exclude=True)

    @model_validator(mode="before")
    @classmethod
    def split_invalid_items(cls, data: Any) -> Any:
        """Проверить transactions, budgets и recurring_payments по одной записи; невалидные — в invalid_items.

        Одна запись с неизвестной категорией или суммой вне диапазона не должна ронять весь батч 422:
        оффлайн-очередь клиента повторяла бы его бесконечно, а пользователь видел бы только общую ошибку.
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        invalid: list[SyncRejectedItem] = []
        models: tuple[tuple[str, type[BaseModel]], ...] = (
            ("transactions", TransactionSyncItem),
            ("budgets", BudgetSyncItem),
            ("recurring_payments", RecurringPaymentSyncItem),
        )
        for key, model in models:
            items = data.get(key)
            if not isinstance(items, list) or len(items) > SYNC_LIST_MAX_LENGTH:
                continue
            valid = []
            for index, raw in enumerate(items):
                try:
                    valid.append(model.model_validate(raw))
                except ValidationError as err:
                    invalid.append(_invalid_sync_item(raw, err, f"{key}.{index}"))
            data[key] = valid
        data["invalid_items"] = invalid
        return data


class SyncResponse(BaseModel):
    """Схема ответа на синхронизацию."""

    synced_transactions: List[TransactionResponse] = Field(..., description="Синхронизированные транзакции")
    synced_budgets: List[BudgetResponse] = Field(..., description="Синхронизированные бюджеты")
    synced_recurring_payments: List[RecurringPaymentResponse] = Field(
        default_factory=list, description="Сохранённые регулярные платежи и доходы"
    )
    rejected: List[SyncRejectedItem] = Field(
        default_factory=list,
        description="Список отклоненных элементов (конфликты ID или чужие записи)",
    )


class SyncChangesResponse(BaseModel):
    """Изменения транзакций с прошлой синхронизации устройства (GET /sync/changes)."""

    transactions: List[TransactionResponse] = Field(
        ..., description="Транзакции, созданные или изменённые после since, по возрастанию времени изменения"
    )
    deleted_transaction_ids: List[uuid.UUID] = Field(
        default_factory=list,
        description="ID транзакций, удалённых после since; только на первой странице",
    )
    has_more: bool = Field(..., description="Есть ли следующая страница")
    next_cursor: Optional[str] = Field(default=None, description="Курсор следующей страницы")
    server_time: datetime = Field(
        ..., description="Время сервера на момент запроса; since следующей синхронизации — значение с первой страницы"
    )


# ============================================================================
# Схемы AI-инсайтов
# ============================================================================


class InsightState(str, enum.Enum):
    """Состояние AI-отчёта за период."""

    ready = "ready"  # Отчёт есть (сгенерирован сейчас или взят из кэша)
    not_generated = "not_generated"  # Отчёта нет, генерация не запрашивалась (generate=false)
    no_data = "no_data"  # Анализировать нечего: будущий месяц или нет операций за период
    # Операций за период меньше MIN_TRANSACTIONS_FOR_INSIGHTS: отчёт не формируется и не отдаётся
    not_enough_data = "not_enough_data"
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
