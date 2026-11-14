"""Модуль описания ORM-моделей базы данных SQLAlchemy."""

import enum
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import JSON, CheckConstraint, Date, DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, Text, func, text
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


class LegalDocument(str, enum.Enum):
    """Юридические документы, принятие которых записывается (версии — `app/legal/versions.py`)."""

    terms = "terms"  # Пользовательское соглашение
    privacy = "privacy"  # Политика конфиденциальности


LEGAL_DOCUMENT_VALUES: tuple[str, ...] = tuple(d.value for d in LegalDocument)


class FamilyRole(str, enum.Enum):
    """Роль участника семьи."""

    owner = "owner"  # Владелец: платит за семейный Pro, приглашает и исключает участников
    member = "member"  # Участник: получает Pro владельца, видит и правит общие записи


FAMILY_ROLE_VALUES: tuple[str, ...] = tuple(r.value for r in FamilyRole)

# Общая запись принадлежит семье; личная — автору. Запись без автора (аккаунт удалён) может быть только общей
_OWNER_OR_FAMILY = "user_id IS NOT NULL OR family_id IS NOT NULL"

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
        accepted_terms_version (str | None): Принятая версия соглашения (дата ISO); None — не принято.
        accepted_privacy_version (str | None): Версия политики, с которой пользователь ознакомлен.
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
    # Последние принятые версии документов: проверка доступа на каждом запросе без чтения истории user_consents
    accepted_terms_version: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    accepted_privacy_version: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)

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
        user_id (uuid.UUID | None): Автор операции (users.id); у личной — владелец. None — общая операция
            участника, удалившего аккаунт («Бывший участник»).
        family_id (uuid.UUID | None): Семья общей операции (families.id); None — личная операция автора.
        amount (Decimal): Сумма операции в валюте чека.
        currency (str): Код валюты операции (USD, EUR).
        exchange_rate (Decimal): Курс валюты операции к базовой валюте автора.
        base_amount (Decimal): Эквивалент суммы в базовой валюте автора.
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
        # Общие операции семьи: по дате (отчёт, бюджеты) и по изменению (GET /sync/changes)
        Index("ix_transactions_family_id_date", "family_id", "date"),
        Index("ix_transactions_family_id_updated_at", "family_id", "updated_at"),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_transactions_currency",
        ),
        CheckConstraint(_OWNER_OR_FAMILY, name="ck_transactions_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Удаление пользователя удаляет его личные операции; общие сервис заранее отвязывает от автора (user_id = NULL)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    # RESTRICT: семью нельзя удалить, пока сервис не раздал её общие операции авторам
    family_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("families.id", ondelete="RESTRICT"), nullable=True)
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


class UserConsent(Base):
    """ORM-модель записи о принятии юридического документа (история согласий, GDPR ст. 7(1)).

    Таблица: user_consents

    Записи только добавляются. Имени, email и IP в записи нет: владельца после удаления аккаунта находит
    только слепой индекс email (тот же HMAC, что `users.email_hash`), если человек сам назовёт адрес.
    Удаление аккаунта не удаляет записи: user_id обнуляется, проставляется account_deleted_at, и запись
    хранится ещё CONSENT_RETENTION_DAYS (`app/services/consent_service.py`) — для защиты от правовых
    претензий (GDPR ст. 17(3)(e)), затем её удаляет регламентная очистка.

    Поля:
        id (uuid.UUID): Идентификатор записи (Primary Key).
        user_id (uuid.UUID | None): Пользователь (users.id); None — аккаунт удалён.
        email_hash (str): Слепой индекс email на момент принятия.
        document (str): Документ — terms или privacy.
        version (str): Принятая версия документа (дата вступления в силу, ISO).
        accepted_at (datetime): Момент принятия (UTC).
        language (str | None): Язык интерфейса приложения (X-App-Language).
        platform (str | None): Платформа и версия ОС (X-Platform).
        app_version (str | None): Версия приложения (X-App-Version).
        account_deleted_at (datetime | None): Момент удаления аккаунта — от него отсчитывается срок хранения.
    """

    __tablename__ = "user_consents"
    __table_args__ = (
        CheckConstraint(f"document IN {LEGAL_DOCUMENT_VALUES}", name="ck_user_consents_document"),
        Index("ix_user_consents_user_id", "user_id"),
        Index("ix_user_consents_email_hash", "email_hash"),
        Index("ix_user_consents_account_deleted_at", "account_deleted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    email_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    document: Mapped[str] = mapped_column(String(20), nullable=False)
    version: Mapped[str] = mapped_column(String(10), nullable=False)
    accepted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    language: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    platform: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    app_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    account_deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class TransactionDeletion(Base):
    """ORM-модель записи об удалённой транзакции.

    Таблица: transaction_deletions

    Удаление транзакции не оставляет следа в transactions, поэтому другие устройства пользователя
    и участники семьи узнают о нём только отсюда (GET /sync/changes). Ключ — id удалённой транзакции: при повторном
    создании транзакции с тем же id запись удаляется. Общая операция, которую автор сделал личной, тоже
    записывается сюда с семьёй: для остальных участников она пропала. Отдаётся только тем, кому операция
    сейчас не видна. Хранится бессрочно, удаляется вместе с пользователем или семьёй.

    Поля:
        transaction_id (uuid.UUID): Идентификатор удалённой транзакции (Primary Key).
        user_id (uuid.UUID): Кто удалил (users.id): автор личной операции или участник семьи.
        family_id (uuid.UUID | None): Семья общей операции (families.id); None — личная операция.
        deleted_at (datetime): Момент удаления.
    """

    __tablename__ = "transaction_deletions"
    __table_args__ = (
        Index("ix_transaction_deletions_user_id_deleted_at", "user_id", "deleted_at"),
        Index("ix_transaction_deletions_family_id_deleted_at", "family_id", "deleted_at"),
    )

    transaction_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    family_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), nullable=True)
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
        user_id (uuid.UUID | None): Автор бюджета (users.id); у личного — владелец. None — общий бюджет
            участника, удалившего аккаунт.
        family_id (uuid.UUID | None): Семья общего бюджета (families.id); None — личный бюджет.
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
        # Один личный лимит пользователя и один общий лимит семьи на категорию и месяц; они не мешают друг другу
        Index(
            "uq_budgets_personal_period",
            "user_id",
            "category",
            "month",
            "year",
            unique=True,
            postgresql_where=text("family_id IS NULL"),
            sqlite_where=text("family_id IS NULL"),
        ),
        Index(
            "uq_budgets_family_period",
            "family_id",
            "category",
            "month",
            "year",
            unique=True,
            postgresql_where=text("family_id IS NOT NULL"),
            sqlite_where=text("family_id IS NOT NULL"),
        ),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_budgets_currency",
        ),
        CheckConstraint(_OWNER_OR_FAMILY, name="ck_budgets_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    family_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("families.id", ondelete="RESTRICT"), nullable=True)
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
        user_id (uuid.UUID | None): Автор (users.id); у личного — владелец. None — общий платёж участника,
            удалившего аккаунт.
        family_id (uuid.UUID | None): Семья общего платежа (families.id); None — личный платёж.
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
        Index("ix_recurring_payments_family_id", "family_id"),
        CheckConstraint(
            f"currency IN {SUPPORTED_CURRENCIES}",
            name="ck_recurring_payments_currency",
        ),
        CheckConstraint("day_of_month BETWEEN 1 AND 31", name="ck_recurring_payments_day_of_month"),
        CheckConstraint(_OWNER_OR_FAMILY, name="ck_recurring_payments_owner"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    family_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("families.id", ondelete="RESTRICT"), nullable=True)
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


class Subscription(Base):
    """ORM-модель подписки Pro пользователя по данным RevenueCat.

    Таблица: subscriptions

    Одна строка на пользователя: снимок доступа Pro (entitlement) из RevenueCat, который обновляется по вебхуку
    и по запросу приложения после покупки. Пользователь RevenueCat — id аккаунта. Строки нет — доступа Pro
    никогда не было или RevenueCat его больше не возвращает; тариф Pro действует, пока не наступил `expires_at`.

    Поля:
        id (uuid.UUID): Идентификатор (Primary Key).
        user_id (uuid.UUID): Внешний ключ на пользователя (users.id), уникален.
        expires_at (datetime | None): Окончание оплаченного периода — дата продления или окончания доступа;
            None — бессрочный доступ (выдан вручную в RevenueCat).
        will_renew (bool): Подписка продлится автоматически (отмена в магазине не обнаружена).
        is_trial (bool): Идёт бесплатный пробный период.
        product_id (str): Идентификатор продукта в магазине (месячная или годовая подписка).
        store (str): Магазин покупки по классификации RevenueCat (play_store, app_store, promotional...);
            manual — Pro выдан вручную.
        family (bool): Есть право семейного Pro (entitlement `family`): можно создать семью, участники получают Pro.
        family_expires_at (datetime | None): Окончание права `family`; None при family — бессрочно.
        updated_at (datetime): Момент последней синхронизации с RevenueCat.
    """

    __tablename__ = "subscriptions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    will_renew: Mapped[bool] = mapped_column(nullable=False)
    is_trial: Mapped[bool] = mapped_column(nullable=False)
    product_id: Mapped[str] = mapped_column(String(100), nullable=False)
    store: Mapped[str] = mapped_column(String(30), nullable=False)
    family: Mapped[bool] = mapped_column(default=False, server_default=text("false"), nullable=False)
    family_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    def expires_at_utc(self) -> datetime | None:
        """Окончание периода с поясом UTC (SQLite отдаёт время без пояса)."""
        return _as_utc(self.expires_at)

    def is_active(self, now: datetime) -> bool:
        """Доступ Pro действует на момент `now`."""
        expires_at = self.expires_at_utc()
        return expires_at is None or expires_at > now

    def family_active(self, now: datetime) -> bool:
        """Право семейного Pro действует на момент `now` (переход на личный Pro его снимает, Pro остаётся)."""
        if not self.family:
            return False
        expires_at = _as_utc(self.family_expires_at)
        return expires_at is None or expires_at > now


def _as_utc(value: datetime | None) -> datetime | None:
    """Время с поясом UTC (SQLite отдаёт время без пояса)."""
    if value is None or value.tzinfo:
        return value
    return value.replace(tzinfo=timezone.utc)


class Family(Base):
    """ORM-модель семьи — группы семейного Pro.

    Таблица: families

    Владелец платит за семейный Pro, участники получают Pro бесплатно, пока право `family` владельца действует.
    Право закончилось — семья заморожена: общие записи только для чтения, новых общих и приглашений нет.
    Владелец уходит или удаляет аккаунт — семья распускается: общие записи достаются авторам как личные.

    Поля:
        id (uuid.UUID): Идентификатор семьи (Primary Key).
        owner_id (uuid.UUID): Владелец (users.id), уникален: у человека одна семья.
        created_at (datetime): Дата создания.
    """

    __tablename__ = "families"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    members: Mapped[list["FamilyMember"]] = relationship(
        back_populates="family", cascade="all, delete-orphan", passive_deletes=True
    )


class FamilyMember(Base):
    """ORM-модель участника семьи (включая владельца с ролью owner).

    Таблица: family_members

    Поля:
        id (uuid.UUID): Идентификатор (Primary Key).
        family_id (uuid.UUID): Семья (families.id).
        user_id (uuid.UUID): Участник (users.id), уникален: человек состоит только в одной семье.
        role (str): owner или member.
        joined_at (datetime): Момент вступления.
    """

    __tablename__ = "family_members"
    __table_args__ = (
        CheckConstraint(f"role IN {FAMILY_ROLE_VALUES}", name="ck_family_members_role"),
        Index("ix_family_members_family_id", "family_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    role: Mapped[str] = mapped_column(String(10), nullable=False)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )

    family: Mapped["Family"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class FamilyInvite(Base):
    """ORM-модель приглашения в семью по одноразовому коду.

    Таблица: family_invites

    Код (8 символов) показывается владельцу один раз при создании, в БД — только его HMAC (`app/pii.py`):
    по утечке базы код не восстановить. Использованное или отозванное приглашение удаляется; истёкшее
    не принимается и не занимает место, удаляется при следующем приглашении семьи.

    Поля:
        id (uuid.UUID): Идентификатор (Primary Key).
        family_id (uuid.UUID): Семья (families.id).
        code_hash (str): HMAC кода, уникален.
        created_at (datetime): Момент создания.
        expires_at (datetime): Окончание действия (через 7 дней).
    """

    __tablename__ = "family_invites"
    __table_args__ = (Index("ix_family_invites_family_id", "family_id"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("families.id", ondelete="CASCADE"), nullable=False)
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    def expires_at_utc(self) -> datetime:
        """Окончание действия с поясом UTC."""
        return _as_utc(self.expires_at)
