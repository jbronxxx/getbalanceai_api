"""Удаление валюты RUB: перевод данных на USD и обновление CHECK-ограничений.

Revision ID: h00700700700
Revises: g00600600600
Create Date: 2026-10-14 16:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h00700700700"
down_revision: Union[str, None] = "g00600600600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_CURRENCIES = "('USD', 'EUR', 'KZT', 'UZS')"
OLD_CURRENCIES = "('RUB', 'USD', 'EUR', 'KZT', 'UZS')"


def _replace_constraints(currencies: str) -> None:
    """Пересоздаёт CHECK-ограничения валют с указанным списком кодов."""
    op.drop_constraint("ck_transactions_currency", "transactions", type_="check")
    op.drop_constraint("ck_users_base_currency", "users", type_="check")
    op.create_check_constraint("ck_users_base_currency", "users", f"base_currency IN {currencies}")
    op.create_check_constraint("ck_transactions_currency", "transactions", f"currency IN {currencies}")


def upgrade() -> None:
    op.drop_constraint("ck_transactions_currency", "transactions", type_="check")
    op.drop_constraint("ck_users_base_currency", "users", type_="check")

    # Существующие данные в RUB переводятся на новую валюту по умолчанию
    op.execute("UPDATE users SET base_currency = 'USD' WHERE base_currency = 'RUB'")
    op.execute("UPDATE transactions SET currency = 'USD' WHERE currency = 'RUB'")

    op.alter_column("users", "base_currency", server_default=sa.text("'USD'"))
    op.alter_column("transactions", "currency", server_default=sa.text("'USD'"))

    op.create_check_constraint("ck_users_base_currency", "users", f"base_currency IN {NEW_CURRENCIES}")
    op.create_check_constraint("ck_transactions_currency", "transactions", f"currency IN {NEW_CURRENCIES}")


def downgrade() -> None:
    # Данные, переведённые на USD, не восстанавливаются: возвращаются только схема и дефолты
    op.alter_column("users", "base_currency", server_default=sa.text("'RUB'"))
    op.alter_column("transactions", "currency", server_default=sa.text("'RUB'"))
    _replace_constraints(OLD_CURRENCIES)
