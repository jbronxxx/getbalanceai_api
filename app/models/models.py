"""Модуль описания ORM-моделей базы данных SQLAlchemy."""

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, CheckConstraint, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.types import TypeDecorator

from app.database import Base
from app.pii import blind_index, get_pii_cipher


class TransactionType(str, enum.Enum):
    """Тип финансовой транзакции."""

    income = "income"  # Доход
    expense = "expense"  # Расход


class Category(str, enum.Enum):
    """Категории доходов и расходов."""

    # Расходные категории
    food = "food"  # Продукты и питание
    transport = "transport"  # Транспорт
    entertainment = "entertainment"  # Развлечения
    health = "health"  # Здоровье и медицина
    subscriptions = "subscriptions"  # Подписки и сервисы
    shopping = "shopping"  # Покупки и одежда
    housing = "housing"  # Жильё и ЖКХ
    communication = "communication"  # Связь: телефон и интернет
    education = "education"  # Образование
    family = "family"  # Семья и дети
    car = "car"  # Автомобиль
    gifts = "gifts"  # Подарки
    travel = "travel"  # Путешествия
    beauty = "beauty"  # Красота и уход

    # Доходные категории
    salary = "salary"  # Заработная плата
    freelance = "freelance"  # Фриланс и подработки
    investments = "investments"  # Инвестиции и дивиденды
    transfers = "transfers"  # Переводы и подарки
    cashback = "cashback"  # Кэшбэк и бонусы
    sales = "sales"  # Продажи

    # Общая категория
    other = "other"  # Прочее


INCOME_CATEGORIES: set[Category] = {
    Category.salary,
    Category.freelance,
    Category.investments,
    Category.transfers,
    Category.cashback,
    Category.sales,
    Category.other,
}

EXPENSE_CATEGORIES: set[Category] = {
    Category.food,
    Category.transport,
    Category.entertainment,
    Category.health,
    Category.subscriptions,
    Category.shopping,
    Category.housing,
    Category.communication,
    Category.education,
    Category.family,
    Category.car,
    Category.gifts,
    Category.travel,
    Category.beauty,
    Category.other,
}


class CurrencyCode(str, enum.Enum):
    """Поддерживаемые валюты системы."""

    USD = "USD"
    EUR = "EUR"


SUPPORTED_CURRENCIES: tuple[str, ...] = tuple(c.value for c in CurrencyCode)

# Наибольшее значение денежной колонки Numeric(18, 2): сумма больше не запишется в БД
MONEY_DB_MAX = Decimal("9999999999999999.99")


class EncryptedString(TypeDecorator):
    """Строка, которая хранится в БД зашифрованной (AES-256-GCM, `app/pii.py`), а в коде видна открытой.

    Шифртекст каждый раз разный, поэтому сравнение колонки в SQL (`==`, `in_`, `like`, сортировка) запрещено —
    иначе запрос молча ничего бы не находил. Поиск — по слепому индексу (`User.email_matches`).
    `max_length` — длина открытого значения, как у прежней колонки `String(n)`.
    """

    impl = Text
    cache_ok = True

    class Comparator(TypeDecorator.Comparator):
        """Запрет SQL-операций над шифртекстом."""

        def operate(self, op, *other, **kwargs):
            raise TypeError("Зашифрованную колонку нельзя сравнивать в SQL: ищите по слепому индексу")

        def reverse_operate(self, op, other, **kwargs):
            raise TypeError("Зашифрованную колонку нельзя сравнивать в SQL: ищите по слепому индексу")

    comparator_factory = Comparator

    def __init__(self, context: str, max_length: int):
        """`context` — имя колонки (`users.email`): входит в AAD, шифртекст не переносится в другую колонку."""
        super().__init__()
        self.context = context
        self.max_length = max_length

    def process_bind_param(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        if len(value) > self.max_length:
            raise ValueError(f"Значение {self.context} длиннее {self.max_length} символов")
        return get_pii_cipher().encrypt(value, self.context)

    def process_result_value(self, value: str | None, dialect) -> str | None:
        if value is None:
            return None
        return get_pii_cipher().decrypt(value, self.context)


class User(Base):
    """ORM-модель пользователя системы.

    Таблица: users

    Поля:
        id (uuid.UUID): Уникальный идентификатор пользователя (Primary Key).
        email (str): Адрес электронной почты (логин); в БД зашифрован.
        email_hash (str): Слепой индекс email (HMAC) — уникален, по нему ищется пользователь при входе.
        name (str): Имя пользователя; в БД зашифровано.
        avatar_url (str | None): URL аватара пользователя.
        base_currency (str): Базовая валюта учета пользователя (USD, EUR).
        created_at (datetime): Дата и время регистрации.
        transactions (list[Transaction]): Список связанных транзакций пользователя.
        budgets (list[Budget]): Список связанных бюджетов пользователя.
        recurring_payments (list[RecurringPayment]): Регулярные платежи и доходы пользователя.
    """

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint(
            f"base_currency IN {SUPPORTED_CURRENCIES}",
            name="ck_users_base_currency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(EncryptedString("users.email", 255), nullable=False)
    email_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(EncryptedString("users.name", 100), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    base_currency: Mapped[str] = mapped_column(
        String(10),
        default=CurrencyCode.USD.value,
        server_default=CurrencyCode.USD.value,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Связи с другими сущностями. Удаление пользователя каскадирует БД (ON DELETE CASCADE): passive_deletes
    # не даёт ORM загружать все транзакции и удалять их по одной
    transactions: Mapped[list["Transaction"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user", cascade="all, delete-orphan", passive_deletes=True)
    recurring_payments: Mapped[list["RecurringPayment"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    tokens: Mapped[list["Token"]] = relationship(back_populates="user", cascade="all, delete-orphan", passive_deletes=True)

    @validates("email")
    def _sync_email_hash(self, key: str, value: str) -> str:
        """Слепой индекс пересчитывается при каждой записи email — и в конструкторе, и при смене адреса."""
        self.email_hash = blind_index(value)
        return value

    @classmethod
    def email_matches(cls, email: str):
        """Условие поиска пользователя по email (без учёта регистра) через слепой индекс."""
        return cls.email_hash == blind_index(email)


class Token(Base):
    """ORM-модель для хранения токенов пользователей.

    Таблица: tokens

    Поля:
        id (uuid.UUID): Уникальный идентификатор токена (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца токена (users.id).
        token_hash (str): Хеш Refresh-токена.
        created_at (datetime): Дата и время создания токена.
        expires_at (datetime): Срок действия токена.
        status (str): active, revoked или expired.
        revoked_at (datetime | None): Момент отзыва (ротация или выход).
        replaced_by_id (uuid.UUID | None): id токена, выданного при ротации этого.
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # Статус токена (active, revoked, expired)
    # Момент отзыва: отличает повтор сразу после ротации от предъявления давно отозванного (украденного) токена
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Преемник по ротации: повтор токена, ответ на ротацию которого не дошёл до клиента, заменяет ещё не
    # использованного преемника новой парой. Без внешнего ключа: очистка токенов удаляет строки пачками,
    # и ON DELETE по неиндексированной ссылке сканировал бы таблицу на каждую удалённую строку
    replaced_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), nullable=True)

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="tokens")


class Transaction(Base):
    """ORM-модель финансовой операции (транзакции).

    Таблица: transactions

    Поля:
        id (uuid.UUID): Уникальный идентификатор транзакции (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца операции (users.id).
        amount (Decimal): Сумма операции в валюте чека.
        currency (str): Код валюты операции (USD, EUR).
        exchange_rate (Decimal): Курс валюты операции к базовой валюте.
        base_amount (Decimal): Эквивалент суммы в базовой валюте пользователя.
        description (str): Описание / комментарий к операции.
        category (Category): Категория транзакции (Enum).
        type (TransactionType): Тип транзакции (income / expense).
        date (datetime): Дата совершения операции.
        created_at (datetime): Дата и время записи в БД.
        updated_at (datetime): Дата и время последнего изменения записи (участвует в ETag списка).
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_id_date", "user_id", "date"),
        Index("ix_transactions_user_id_created_at", "user_id", "created_at"),
        Index("ix_transactions_user_id_type_date", "user_id", "type", "date"),
        # Выборка изменений с момента прошлой синхронизации (GET /sync/changes)
        Index("ix_transactions_user_id_updated_at", "user_id", "updated_at"),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_transactions_currency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", server_default="USD", nullable=False)
    exchange_rate: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=6), default=Decimal("1.0"), server_default="1.0", nullable=False
    )
    base_amount: Mapped[Decimal] = mapped_column(
        Numeric(precision=18, scale=2), default=Decimal("0.0"), server_default="0.0", nullable=False
    )
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    # onupdate срабатывает и при ORM-правке, и при пакетном UPDATE (пересчёт при смене базовой валюты)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="transactions")


class TransactionDeletion(Base):
    """ORM-модель записи об удалённой транзакции.

    Таблица: transaction_deletions

    Удаление транзакции не оставляет следа в transactions, поэтому другие устройства пользователя
    узнают о нём только отсюда (GET /sync/changes). Ключ — id удалённой транзакции: при повторном
    создании транзакции с тем же id запись удаляется. Хранится бессрочно, удаляется вместе с пользователем.

    Поля:
        transaction_id (uuid.UUID): Идентификатор удалённой транзакции (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца транзакции (users.id).
        deleted_at (datetime): Момент удаления.
    """

    __tablename__ = "transaction_deletions"
    __table_args__ = (Index("ix_transaction_deletions_user_id_deleted_at", "user_id", "deleted_at"),)

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    deleted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )


class Budget(Base):
    """ORM-модель бюджета (лимита расходов по категории).

    Таблица: budgets

    Поля:
        id (uuid.UUID): Уникальный идентификатор бюджета (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца бюджета (users.id).
        category (Category): Категория расходов, для которой установлен лимит.
        limit_amount (Decimal): Установленный лимит суммы на месяц в валюте лимита.
        currency (str): Валюта лимита (USD, EUR); не зависит от базовой валюты пользователя.
        month (int): Месяц действия бюджета (1-12).
        year (int): Год действия бюджета.
        created_at (datetime): Дата создания бюджета.
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category", "month", "year", name="uq_budget_user_cat_period"),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_budgets_currency",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="USD", server_default="USD", nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    year: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="budgets")


class RecurringPayment(Base):
    """ORM-модель регулярного платежа или дохода (ЖКХ, кредит, связь, зарплата).

    Таблица: recurring_payments

    Расписание, а не операция: в день платежа приложение напоминает о нём, а запись создаёт пользователь
    («Оплачено»). Запись месяца получает детерминированный id (платёж + месяц) — оплата с двух устройств
    не создаёт дублей, а оплаченный месяц узнаётся по наличию записи. Удаление платежа прошлые записи
    не трогает.

    Поля:
        id (uuid.UUID): Идентификатор (Primary Key), создаётся на клиенте.
        user_id (uuid.UUID): Внешний ключ на владельца (users.id).
        type (TransactionType): Расход или доход.
        category (Category): Категория будущих записей.
        amount (Decimal): Ожидаемая сумма в валюте платежа (при оплате её можно исправить).
        currency (str): Валюта платежа (USD, EUR).
        description (str): Название платежа («Интернет»); может быть пустым — тогда показывается категория.
        day_of_month (int): День месяца 1-31; в коротком месяце — его последний день.
        start_date (date): Первый день платежа.
        end_date (date | None): Последний день платежа (кредит на 12 месяцев); None — без срока.
        skipped_periods (list[str]): Пропущенные месяцы «YYYY-MM» — о них не напоминать.
        created_at (datetime): Дата создания.
        updated_at (datetime): Дата последнего изменения (участвует в ETag списка).
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "recurring_payments"
    __table_args__ = (
        Index("ix_recurring_payments_user_id", "user_id"),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_recurring_payments_currency",
        ),
        CheckConstraint("day_of_month BETWEEN 1 AND 31", name="ck_recurring_payments_day_of_month"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=18, scale=2), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)
    description: Mapped[str] = mapped_column(String(255), default="", server_default="", nullable=False)
    day_of_month: Mapped[int] = mapped_column(nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    skipped_periods: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped["User"] = relationship(back_populates="recurring_payments")
