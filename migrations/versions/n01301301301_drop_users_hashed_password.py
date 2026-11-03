"""Удаление колонки users.hashed_password.

Вход по паролю выведен из API, хеши очищены миграцией j00900900900. Колонка удаляется отдельным
релизом: в предыдущей версии она исключена из маппера, и работающий во время деплоя контейнер
её не читает и не пишет.

Revision ID: n01301301301
Revises: m01201201201
Create Date: 2026-11-03 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "n01301301301"
down_revision: Union[str, None] = "m01201201201"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "hashed_password")


def downgrade() -> None:
    # Значения не восстанавливаются: к моменту удаления колонки они уже были очищены
    op.add_column("users", sa.Column("hashed_password", sa.String(length=255), nullable=True))
