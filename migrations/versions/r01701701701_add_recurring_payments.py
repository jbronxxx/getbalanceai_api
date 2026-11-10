"""Регулярные платежи и доходы (ЖКХ, кредиты, связь, зарплата).

Таблица использует существующие типы enum category и transactiontype — новых типов не создаёт.

Revision ID: r01701701701
Revises: q01601601601
Create Date: 2026-11-10 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "r01701701701"
down_revision: Union[str, None] = "q01601601601"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SUPPORTED_CURRENCIES = ("USD", "EUR", "KZT", "UZS")


def upgrade() -> None:
    op.create_table(
        "recurring_payments",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("type", postgresql.ENUM(name="transactiontype", create_type=False), nullable=False),
        sa.Column("category", postgresql.ENUM(name="category", create_type=False), nullable=False),
        sa.Column("amount", sa.Numeric(precision=18, scale=2), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=False),
        sa.Column("description", sa.String(length=255), server_default="", nullable=False),
        sa.Column("day_of_month", sa.Integer(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("skipped_periods", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(f"currency IN {SUPPORTED_CURRENCIES}", name="ck_recurring_payments_currency"),
        sa.CheckConstraint("day_of_month BETWEEN 1 AND 31", name="ck_recurring_payments_day_of_month"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recurring_payments_user_id", "recurring_payments", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_recurring_payments_user_id", table_name="recurring_payments")
    op.drop_table("recurring_payments")
