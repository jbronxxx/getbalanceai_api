"""Новые категории расходов в перечислении category.

Жильё и ЖКХ, связь, образование, семья, авто, подарки, путешествия, красота.
ALTER TYPE … ADD VALUE не выполняется внутри транзакции в старых PostgreSQL, поэтому — в autocommit-блоке.

Revision ID: q01601601601
Revises: p01501501501
Create Date: 2026-11-10 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q01601601601"
down_revision: Union[str, None] = "p01501501501"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


NEW_EXPENSE_CATEGORIES = (
    "housing",
    "communication",
    "education",
    "family",
    "car",
    "gifts",
    "travel",
    "beauty",
)


def upgrade() -> None:
    """Добавить новые значения категорий расходов в тип enum category."""
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        with op.get_context().autocommit_block():
            for val in NEW_EXPENSE_CATEGORIES:
                op.execute(sa.text(f"ALTER TYPE category ADD VALUE IF NOT EXISTS '{val}'"))


def downgrade() -> None:
    """Удалить значение из enum в PostgreSQL нельзя без пересоздания типа — откат не выполняется."""
    pass
