"""Расширение разрядности денежных сумм до 18 знаков (NUMERIC(18, 2) и NUMERIC(18, 6)).

Предотвращает переполнение разрядности при работе с валютами крупного номинала (например, UZS).

Внимание по downgrade():
Откат миграции к NUMERIC(12, 2) завершится ошибкой NumericValueOutOfRange в СУБД,
если в таблицах transactions или budgets присутствуют суммы, превышающие 10^10
(9 999 999 999.99). Перед откатом в производственной среде необходимо выполнить
предварительную проверку и санитизацию значений.

Revision ID: e00400400400
Revises: d00300300300
Create Date: 2026-10-12 10:15:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e00400400400"
down_revision: Union[str, None] = "d00300300300"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. Расширение точности суммы транзакции (amount) до Numeric(18, 2)
    op.alter_column(
        "transactions",
        "amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        type_=sa.Numeric(precision=18, scale=2),
        existing_nullable=False,
    )

    # 2. Расширение точности суммы в базовой валюте (base_amount) до Numeric(18, 2)
    op.alter_column(
        "transactions",
        "base_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        type_=sa.Numeric(precision=18, scale=2),
        existing_nullable=False,
        existing_server_default="0.0",
    )

    # 3. Расширение точности курса валюты (exchange_rate) до Numeric(18, 6)
    op.alter_column(
        "transactions",
        "exchange_rate",
        existing_type=sa.Numeric(precision=14, scale=6),
        type_=sa.Numeric(precision=18, scale=6),
        existing_nullable=False,
        existing_server_default="1.0",
    )

    # 4. Расширение точности лимита бюджета (limit_amount) до Numeric(18, 2)
    op.alter_column(
        "budgets",
        "limit_amount",
        existing_type=sa.Numeric(precision=12, scale=2),
        type_=sa.Numeric(precision=18, scale=2),
        existing_nullable=False,
    )


def downgrade() -> None:
    # 1. Откат лимита бюджета (limit_amount) к Numeric(12, 2)
    op.alter_column(
        "budgets",
        "limit_amount",
        existing_type=sa.Numeric(precision=18, scale=2),
        type_=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
    )

    # 2. Откат курса валюты (exchange_rate) к Numeric(14, 6)
    op.alter_column(
        "transactions",
        "exchange_rate",
        existing_type=sa.Numeric(precision=18, scale=6),
        type_=sa.Numeric(precision=14, scale=6),
        existing_nullable=False,
        existing_server_default="1.0",
    )

    # 3. Откат базовой суммы (base_amount) к Numeric(12, 2)
    op.alter_column(
        "transactions",
        "base_amount",
        existing_type=sa.Numeric(precision=18, scale=2),
        type_=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
        existing_server_default="0.0",
    )

    # 4. Откат суммы транзакции (amount) к Numeric(12, 2)
    op.alter_column(
        "transactions",
        "amount",
        existing_type=sa.Numeric(precision=18, scale=2),
        type_=sa.Numeric(precision=12, scale=2),
        existing_nullable=False,
    )
