"""Модуль описания ORM-моделей базы данных SQLAlchemy."""

import enum
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy import ForeignKey, Index, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


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
    Category.other,
}


class User(Base):
    """ORM-модель пользователя системы.

    Таблица: users

    Поля:
        id (uuid.UUID): Уникальный идентификатор пользователя (Primary Key).
        email (str): Уникальный адрес электронной почты (логин).
        hashed_password (str): Хеш пароля (bcrypt).
        name (str): Имя пользователя.
        created_at (datetime): Дата и время регистрации.
        transactions (list[Transaction]): Список связанных транзакций пользователя.
        budgets (list[Budget]): Список связанных бюджетов пользователя.
    """

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Связи с другими сущностями
    transactions: Mapped[list["Transaction"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    budgets: Mapped[list["Budget"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    tokens: Mapped[list["Token"]] = relationship(back_populates="user", cascade="all, delete-orphan")


class Token(Base):
    """ORM-модель для хранения токенов пользователей.

    Таблица: tokens

    Поля:
        id (uuid.UUID): Уникальный идентификатор токена (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца токена (users.id).
        token_hash (str): Хеш Refresh-токена.
        created_at (datetime): Дата и время создания токена.
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(500), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="active")  # Статус токена (active, revoked, expired)

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="tokens")


class Transaction(Base):
    """ORM-модель финансовой операции (транзакции).

    Таблица: transactions

    Поля:
        id (uuid.UUID): Уникальный идентификатор транзакции (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца операции (users.id).
        amount (Decimal): Сумма операции.
        description (str): Описание / комментарий к операции.
        category (Category): Категория транзакции (Enum).
        type (TransactionType): Тип транзакции (income / expense).
        date (datetime): Дата совершения операции.
        created_at (datetime): Дата и время записи в БД.
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "transactions"
    __table_args__ = (
        Index("ix_transactions_user_id_date", "user_id", "date"),
        Index("ix_transactions_user_id_created_at", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    type: Mapped[TransactionType] = mapped_column(SAEnum(TransactionType), nullable=False)
    date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="transactions")


class Budget(Base):
    """ORM-модель бюджета (лимита расходов по категории).

    Таблица: budgets

    Поля:
        id (uuid.UUID): Уникальный идентификатор бюджета (Primary Key).
        user_id (uuid.UUID): Внешний ключ на владельца бюджета (users.id).
        category (Category): Категория расходов, для которой установлен лимит.
        limit_amount (Decimal): Установленный лимит суммы на месяц.
        month (int): Месяц действия бюджета (1-12).
        year (int): Год действия бюджета.
        created_at (datetime): Дата создания бюджета.
        user (User): Связанный объект пользователя.
    """

    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("user_id", "category", "month", "year", name="uq_budget_user_cat_period"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False)
    category: Mapped[Category] = mapped_column(SAEnum(Category), nullable=False)
    limit_amount: Mapped[Decimal] = mapped_column(Numeric(precision=12, scale=2), nullable=False)
    month: Mapped[int] = mapped_column(nullable=False)
    year: Mapped[int] = mapped_column(nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))

    # Связь с пользователем
    user: Mapped["User"] = relationship(back_populates="budgets")
