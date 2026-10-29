"""Перевод email пользователей в нижний регистр.

Вход через провайдеров ищет пользователя по email в нижнем регистре. Адрес, у которого уже есть
двойник в нижнем регистре, не меняется: уникальность колонки не даст их совместить, а вход
по-прежнему попадёт в аккаунт с адресом в нижнем регистре.

Revision ID: l01101101101
Revises: k01001001001
Create Date: 2026-10-29 19:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "l01101101101"
down_revision: Union[str, None] = "k01001001001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE users SET email = lower(email)
        WHERE email <> lower(email)
          AND NOT EXISTS (SELECT 1 FROM users AS twin WHERE twin.email = lower(users.email))
        """
    )


def downgrade() -> None:
    # Исходный регистр не сохранялся; email в нижнем регистре остаются корректными адресами
    pass
