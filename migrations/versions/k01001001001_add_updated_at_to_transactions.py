"""Время последнего изменения транзакции для ETag списка транзакций.

ETag списка считался по количеству и максимальной дате создания: правка суммы, категории или даты
не меняла его, и другие устройства получали 304. Существующим строкам updated_at = created_at.

Revision ID: k01001001001
Revises: j00900900900
Create Date: 2026-10-29 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k01001001001"
down_revision: Union[str, None] = "j00900900900"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default: строки, которые вставит старый контейнер во время деплоя, тоже получат значение
    op.add_column(
        "transactions",
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )
    op.execute("UPDATE transactions SET updated_at = created_at WHERE created_at IS NOT NULL")


def downgrade() -> None:
    op.drop_column("transactions", "updated_at")
