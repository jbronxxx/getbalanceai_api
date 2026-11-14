"""Семейный Pro (FEAT-03): семья, участники, приглашения и отметка «общая» у записей.

* `families`, `family_members`, `family_invites` — новые пустые таблицы.
* `transactions`, `budgets`, `recurring_payments`: `family_id` (NULL — личная запись, как у всех существующих) и
  `user_id` допускает NULL — общая запись участника, удалившего аккаунт; проверка «есть автор или семья».
  FK на семью — RESTRICT: семью удаляет сервис, раздав общие записи авторам.
* `budgets`: уникальность (user_id, category, month, year) заменена двумя частичными уникальными индексами — для
  личных лимитов (по автору) и для общих (по семье). Существующие лимиты личные и ограничение не нарушают.
* `transaction_deletions.family_id` — удаления общих операций для всех участников семьи.
* `subscriptions.family`, `family_expires_at` — право семейного Pro (по умолчанию false).
* Индексы `transactions` по семье создаются CONCURRENTLY: таблица большая, запись не блокируется.

downgrade() теряет данные семей: общие записи без автора удаляются, общие лимиты удаляются (могли бы нарушить
прежнюю уникальность), остальные общие записи остаются личными у авторов.

Revision ID: 0950ddf83fce
Revises: 68917fd40f73
Create Date: 2026-11-14 10:45:08.107285

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0950ddf83fce"
down_revision: Union[str, None] = "68917fd40f73"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OWNER_OR_FAMILY = "user_id IS NOT NULL OR family_id IS NOT NULL"
_SHARED_TABLES = ("transactions", "budgets", "recurring_payments")


def upgrade() -> None:
    op.create_table(
        "families",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("owner_id", sa.UUID(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_id"),
    )
    op.create_table(
        "family_invites",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code_hash"),
    )
    op.create_index("ix_family_invites_family_id", "family_invites", ["family_id"], unique=False)
    op.create_table(
        "family_members",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("family_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("role", sa.String(length=10), nullable=False),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("role IN ('owner', 'member')", name="ck_family_members_role"),
        sa.ForeignKeyConstraint(["family_id"], ["families.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index("ix_family_members_family_id", "family_members", ["family_id"], unique=False)

    for table in _SHARED_TABLES:
        op.add_column(table, sa.Column("family_id", sa.UUID(), nullable=True))
        op.alter_column(table, "user_id", existing_type=sa.UUID(), nullable=True)
        op.create_foreign_key(f"{table}_family_id_fkey", table, "families", ["family_id"], ["id"], ondelete="RESTRICT")
        op.create_check_constraint(f"ck_{table}_owner", table, _OWNER_OR_FAMILY)

    op.drop_constraint("uq_budget_user_cat_period", "budgets", type_="unique")
    op.create_index(
        "uq_budgets_personal_period",
        "budgets",
        ["user_id", "category", "month", "year"],
        unique=True,
        postgresql_where=sa.text("family_id IS NULL"),
        sqlite_where=sa.text("family_id IS NULL"),
    )
    op.create_index(
        "uq_budgets_family_period",
        "budgets",
        ["family_id", "category", "month", "year"],
        unique=True,
        postgresql_where=sa.text("family_id IS NOT NULL"),
        sqlite_where=sa.text("family_id IS NOT NULL"),
    )
    op.create_index("ix_recurring_payments_family_id", "recurring_payments", ["family_id"], unique=False)

    op.add_column("subscriptions", sa.Column("family", sa.Boolean(), server_default=sa.text("false"), nullable=False))
    op.add_column("subscriptions", sa.Column("family_expires_at", sa.DateTime(timezone=True), nullable=True))

    op.add_column("transaction_deletions", sa.Column("family_id", sa.UUID(), nullable=True))
    op.create_foreign_key(
        "transaction_deletions_family_id_fkey",
        "transaction_deletions",
        "families",
        ["family_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "ix_transaction_deletions_family_id_deleted_at",
        "transaction_deletions",
        ["family_id", "deleted_at"],
        unique=False,
    )

    with op.get_context().autocommit_block():
        op.create_index(
            "ix_transactions_family_id_date",
            "transactions",
            ["family_id", "date"],
            unique=False,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_transactions_family_id_updated_at",
            "transactions",
            ["family_id", "updated_at"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index("ix_transactions_family_id_updated_at", table_name="transactions", postgresql_concurrently=True)
        op.drop_index("ix_transactions_family_id_date", table_name="transactions", postgresql_concurrently=True)

    op.drop_index("ix_transaction_deletions_family_id_deleted_at", table_name="transaction_deletions")
    op.drop_constraint("transaction_deletions_family_id_fkey", "transaction_deletions", type_="foreignkey")
    op.drop_column("transaction_deletions", "family_id")

    op.drop_column("subscriptions", "family_expires_at")
    op.drop_column("subscriptions", "family")

    op.drop_index("ix_recurring_payments_family_id", table_name="recurring_payments")
    # Общие лимиты могли бы совпасть с личными автора по прежнему ключу уникальности
    op.execute("DELETE FROM budgets WHERE family_id IS NOT NULL")
    op.drop_index("uq_budgets_family_period", table_name="budgets")
    op.drop_index("uq_budgets_personal_period", table_name="budgets")

    for table in _SHARED_TABLES:
        op.execute(f"DELETE FROM {table} WHERE user_id IS NULL")
        op.drop_constraint(f"ck_{table}_owner", table, type_="check")
        op.drop_constraint(f"{table}_family_id_fkey", table, type_="foreignkey")
        op.alter_column(table, "user_id", existing_type=sa.UUID(), nullable=False)
        op.drop_column(table, "family_id")

    op.create_unique_constraint("uq_budget_user_cat_period", "budgets", ["user_id", "category", "month", "year"])

    op.drop_index("ix_family_members_family_id", table_name="family_members")
    op.drop_table("family_members")
    op.drop_index("ix_family_invites_family_id", table_name="family_invites")
    op.drop_table("family_invites")
    op.drop_table("families")
