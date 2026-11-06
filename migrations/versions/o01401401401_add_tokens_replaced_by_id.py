"""Преемник refresh-токена по ротации.

replaced_by_id связывает ротированный токен с выданным вместо него. Если ответ на ротацию не дошёл
до клиента и тот повторил запрос с прежним токеном в окне REFRESH_REUSE_GRACE, ещё не использованный
преемник отзывается и выдаётся новая пара — вместо 401 и выхода из аккаунта. Колонка nullable: у токенов,
ротированных до миграции, преемник неизвестен, и их повтор отклоняется по прежним правилам.

Revision ID: o01401401401
Revises: n01301301301
Create Date: 2026-11-06 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "o01401401401"
down_revision: Union[str, None] = "n01301301301"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("tokens", sa.Column("replaced_by_id", sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column("tokens", "replaced_by_id")
