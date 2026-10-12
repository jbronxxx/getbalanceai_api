"""Добавление CHECK-ограничений допустимых кодов валют в таблицы users и transactions.

Revision ID: f00500500500
Revises: e00400400400
Create Date: 2026-10-12 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f00500500500"
down_revision: Union[str, None] = "e00400400400"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Ограничение допустимых кодов валют для users.base_currency
    op.create_check_constraint(
        "ck_users_base_currency",
        "users",
        "base_currency IN ('RUB', 'USD', 'EUR', 'KZT', 'UZS')",
    )
    # Ограничение допустимых кодов валют для transactions.currency
    op.create_check_constraint(
        "ck_transactions_currency",
        "transactions",
        "currency IN ('RUB', 'USD', 'EUR', 'KZT', 'UZS')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_transactions_currency", "transactions", type_="check")
    op.drop_constraint("ck_users_base_currency", "users", type_="check")
