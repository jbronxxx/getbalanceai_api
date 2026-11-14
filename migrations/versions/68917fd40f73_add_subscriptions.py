"""Подписка Pro из RevenueCat (FEAT-02).

* `subscriptions` — снимок доступа Pro пользователя (одна строка на пользователя): срок, автопродление, пробный
  период, продукт и магазин. Таблица новая и пустая: у существующих пользователей тариф Free.

Revision ID: 68917fd40f73
Revises: 7258a8ce0788
Create Date: 2026-11-13 21:29:50.940033

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "68917fd40f73"
down_revision: Union[str, None] = "7258a8ce0788"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subscriptions",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("will_renew", sa.Boolean(), nullable=False),
        sa.Column("is_trial", sa.Boolean(), nullable=False),
        sa.Column("product_id", sa.String(length=100), nullable=False),
        sa.Column("store", sa.String(length=30), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("subscriptions")
