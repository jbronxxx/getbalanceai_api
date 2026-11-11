"""Начальная схема базы данных (история миграций сжата в одну 11.11.2026).

Валюты — только USD и EUR. Существующая база переводится на эту ревизию без пересоздания:
данные в удалённых валютах очищаются и ревизия записывается в alembic_version вручную
(порядок — в чек-листе выкатки MKT-01).

Revision ID: t01901901901
Revises:
Create Date: 2026-11-11 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "t01901901901"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Порядок значений — как в существующей базе (значения добавлялись по мере появления категорий)
CATEGORIES = (
    "food",
    "transport",
    "entertainment",
    "health",
    "subscriptions",
    "shopping",
    "salary",
    "other",
    "freelance",
    "investments",
    "transfers",
    "cashback",
    "sales",
    "housing",
    "communication",
    "education",
    "family",
    "car",
    "gifts",
    "travel",
    "beauty",
)
TRANSACTION_TYPES = ("income", "expense")

# Типы создаются один раз до таблиц: ими пользуются transactions, budgets и recurring_payments
category_enum = postgresql.ENUM(*CATEGORIES, name="category", create_type=False)
transaction_type_enum = postgresql.ENUM(*TRANSACTION_TYPES, name="transactiontype", create_type=False)


def upgrade() -> None:
    bind = op.get_bind()
    category_enum.create(bind, checkfirst=True)
    transaction_type_enum.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("avatar_url", sa.String(length=500), nullable=True),
        sa.Column("base_currency", sa.String(length=10), server_default="USD", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("base_currency IN ('USD', 'EUR')", name="ck_users_base_currency"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "budgets",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("limit_amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="USD", nullable=False),
        sa.Column("month", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("currency IN ('USD', 'EUR')", name="ck_budgets_currency"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "category", "month", "year", name="uq_budget_user_cat_period"),
    )
    op.create_table(
        "recurring_payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", transaction_type_enum, nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("skipped_periods", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("currency IN ('USD', 'EUR')", name="ck_recurring_payments_currency"),
        sa.CheckConstraint("day_of_month BETWEEN 1 AND 31", name="ck_recurring_payments_day_of_month"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recurring_payments_user_id", "recurring_payments", ["user_id"], unique=False)
    op.create_table(
        "tokens",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("token_hash", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("replaced_by_id", sa.UUID(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("token_hash", name="uq_tokens_token_hash"),
    )
    op.create_table(
        "transaction_deletions",
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index(
        "ix_transaction_deletions_user_id_deleted_at", "transaction_deletions", ["user_id", "deleted_at"], unique=False
    )
    op.create_table(
        "transactions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), server_default="USD", nullable=False),
        sa.Column("exchange_rate", sa.Numeric(precision=18, scale=6), server_default="1.0", nullable=False),
        sa.Column("base_amount", sa.Numeric(precision=18, scale=2), server_default="0.0", nullable=False),
        sa.Column("description", sa.String(length=255), nullable=False),
        sa.Column("category", category_enum, nullable=False),
        sa.Column("type", transaction_type_enum, nullable=False),
        sa.Column("date", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("currency IN ('USD', 'EUR')", name="ck_transactions_currency"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_transactions_user_id_created_at", "transactions", ["user_id", "created_at"], unique=False)
    op.create_index("ix_transactions_user_id_date", "transactions", ["user_id", "date"], unique=False)
    op.create_index("ix_transactions_user_id_type_date", "transactions", ["user_id", "type", "date"], unique=False)
    op.create_index("ix_transactions_user_id_updated_at", "transactions", ["user_id", "updated_at"], unique=False)


def downgrade() -> None:
    op.drop_table("transactions")
    op.drop_table("transaction_deletions")
    op.drop_table("tokens")
    op.drop_table("recurring_payments")
    op.drop_table("budgets")
    op.drop_table("users")
    bind = op.get_bind()
    transaction_type_enum.drop(bind, checkfirst=True)
    category_enum.drop(bind, checkfirst=True)
