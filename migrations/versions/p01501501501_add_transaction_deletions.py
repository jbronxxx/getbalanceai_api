"""Журнал удалённых транзакций и индекс по времени изменения для GET /sync/changes.

Клиент забирает только изменения с прошлой синхронизации: транзакции с updated_at позже отметки и id
удалённых после неё. Удаление не оставляет следа в transactions, поэтому для него нужна отдельная таблица.
Индекс (user_id, updated_at) на большой таблице строится CONCURRENTLY — без блокировки записи.

Revision ID: p01501501501
Revises: o01401401401
Create Date: 2026-11-06 19:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "p01501501501"
down_revision: Union[str, None] = "o01401401401"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "transaction_deletions",
        sa.Column("transaction_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("transaction_id"),
    )
    op.create_index(
        "ix_transaction_deletions_user_id_deleted_at",
        "transaction_deletions",
        ["user_id", "deleted_at"],
        unique=False,
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_transactions_user_id_updated_at",
            "transactions",
            ["user_id", "updated_at"],
            unique=False,
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_transactions_user_id_updated_at",
            table_name="transactions",
            postgresql_concurrently=True,
        )
    op.drop_index("ix_transaction_deletions_user_id_deleted_at", table_name="transaction_deletions")
    op.drop_table("transaction_deletions")
