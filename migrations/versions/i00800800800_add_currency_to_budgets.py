"""Добавление валюты лимита в таблицу budgets.

Существующие лимиты велись в базовой валюте пользователя, поэтому им проставляется
текущая base_currency владельца. Дальше валюта лимита не меняется при смене базовой.

Revision ID: i00800800800
Revises: h00700700700
Create Date: 2026-10-16 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i00800800800"
down_revision: Union[str, None] = "h00700700700"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CURRENCIES = "('USD', 'EUR', 'KZT', 'UZS')"


def upgrade() -> None:
    op.add_column(
        "budgets",
        sa.Column("currency", sa.String(length=10), nullable=False, server_default=sa.text("'USD'")),
    )
    op.execute("UPDATE budgets SET currency = (SELECT users.base_currency FROM users WHERE users.id = budgets.user_id)")
    op.create_check_constraint("ck_budgets_currency", "budgets", f"currency IN {CURRENCIES}")


def downgrade() -> None:
    # Лимиты в валюте, отличной от базовой, сохраняют сумму как есть: курс на момент отката неизвестен
    op.drop_constraint("ck_budgets_currency", "budgets", type_="check")
    op.drop_column("budgets", "currency")
