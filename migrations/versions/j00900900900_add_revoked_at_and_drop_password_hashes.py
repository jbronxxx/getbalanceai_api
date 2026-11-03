"""Время отзыва refresh-токена и удаление хешей паролей.

revoked_at нужен для обнаружения повторного использования refresh-токена: повтор сразу после
ротации (клиент не получил ответ) отличается от предъявления давно отозванного токена.
Вход по паролю выведен из API, поэтому хеши паролей больше не нужны и удаляются; сама колонка
users.hashed_password остаётся до следующего релиза, чтобы работающий во время деплоя
старый контейнер не падал на отсутствующей колонке.

Revision ID: j00900900900
Revises: i00800800800
Create Date: 2026-10-29 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j00900900900"
down_revision: Union[str, None] = "i00800800800"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True))
    op.execute("UPDATE users SET hashed_password = NULL WHERE hashed_password IS NOT NULL")


def downgrade() -> None:
    # Хеши паролей не восстанавливаются: при необходимости — из бэкапа predeploy
    op.drop_column("tokens", "revoked_at")
