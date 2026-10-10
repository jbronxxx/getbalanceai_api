"""Добавление новых категорий доходов в перечисление category.

Revision ID: 84a0908f991c
Revises: b00100100100
Create Date: 2026-10-11 02:02:04.700354

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "84a0908f991c"
down_revision: Union[str, None] = "b00100100100"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_INCOME_CATEGORIES = (
    "freelance",
    "investments",
    "transfers",
    "cashback",
    "sales",
)


def upgrade() -> None:
    """Добавить новые значения категорий доходов в тип enum category."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for val in NEW_INCOME_CATEGORIES:
                op.execute(sa.text(f"ALTER TYPE category ADD VALUE IF NOT EXISTS '{val}'"))


def downgrade() -> None:
    """Откат добавления значений enum в PostgreSQL не поддерживается напрямую."""
    pass
