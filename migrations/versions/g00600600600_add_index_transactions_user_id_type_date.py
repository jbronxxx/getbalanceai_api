"""Добавление составного индекса (user_id, type, date) для ускорения агрегации расходов бюджетов.

Revision ID: g00600600600
Revises: f00500500500
Create Date: 2026-10-12 12:45:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g00600600600"
down_revision: Union[str, None] = "f00500500500"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Составной индекс для эффективной фильтрации расходов пользователя по диапазону дат
    op.create_index(
        "ix_transactions_user_id_type_date",
        "transactions",
        ["user_id", "type", "date"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_transactions_user_id_type_date", table_name="transactions")
