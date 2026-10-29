"""ON DELETE CASCADE для внешних ключей на users.

Удаление аккаунта раньше каскадировал ORM: загружал все транзакции, бюджеты и токены пользователя
в память и удалял их по одной. Теперь это делает БД одним DELETE пользователя.

Revision ID: m01201201201
Revises: l01101101101
Create Date: 2026-10-29 21:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "m01201201201"
down_revision: Union[str, None] = "l01101101101"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("transactions", "budgets", "tokens")


def upgrade() -> None:
    for table in TABLES:
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    for table in TABLES:
        op.drop_constraint(f"{table}_user_id_fkey", table, type_="foreignkey")
        op.create_foreign_key(f"{table}_user_id_fkey", table, "users", ["user_id"], ["id"])
