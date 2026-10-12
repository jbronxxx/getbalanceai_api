"""Добавление колонок мультивалютности (currency, exchange_rate, base_amount) в таблицу transactions.

Внимание по заполнению исторических данных:
Операция UPDATE transactions SET base_amount = amount WHERE base_amount = 0.0 в upgrade()
выполняется единой транзакцией на всей таблице. Для высоконагруженных таблиц в production
при последующих подобных миграциях необходимо разбивать обновления на пакеты (батчи)
с ограничением LIMIT, чтобы исключить длительные блокировки строк.

Revision ID: c00200200200
Revises: 84a0908f991c
Create Date: 2026-10-11 19:30:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c00200200200"
down_revision: Union[str, None] = "84a0908f991c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "transactions",
        sa.Column("currency", sa.String(length=10), server_default="RUB", nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("exchange_rate", sa.Numeric(precision=14, scale=6), server_default="1.0", nullable=False),
    )
    op.add_column(
        "transactions",
        sa.Column("base_amount", sa.Numeric(precision=12, scale=2), server_default="0.0", nullable=False),
    )
    op.execute("UPDATE transactions SET base_amount = amount WHERE base_amount = 0.0")


def downgrade() -> None:
    op.drop_column("transactions", "base_amount")
    op.drop_column("transactions", "exchange_rate")
    op.drop_column("transactions", "currency")
