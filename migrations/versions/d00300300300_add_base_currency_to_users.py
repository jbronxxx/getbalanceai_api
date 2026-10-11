"""Add base_currency column to users

Revision ID: d00300300300
Revises: c00200200200
Create Date: 2026-10-11 22:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d00300300300"
down_revision: Union[str, None] = "c00200200200"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("base_currency", sa.String(length=10), server_default="RUB", nullable=False),
    )


def downgrade() -> None:
    op.drop_column("users", "base_currency")
