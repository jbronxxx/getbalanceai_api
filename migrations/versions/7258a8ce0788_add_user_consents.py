"""Принятие юридических документов (LEG-01).

* `user_consents` — история принятия соглашения и политики: записи только добавляются; при удалении аккаунта
  `user_id` обнуляется (ON DELETE SET NULL), запись хранится до регламентной очистки.
* `users.accepted_terms_version`, `users.accepted_privacy_version` — последние принятые версии. У существующих
  пользователей пусто: после деплоя они примут текущие версии на экране новой версии в приложении.

Revision ID: 7258a8ce0788
Revises: 11c4d089e0f6
Create Date: 2026-11-13 12:27:52.524981

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7258a8ce0788"
down_revision: Union[str, None] = "11c4d089e0f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_consents",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("email_hash", sa.String(length=64), nullable=False),
        sa.Column("document", sa.String(length=20), nullable=False),
        sa.Column("version", sa.String(length=10), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("language", sa.String(length=64), nullable=True),
        sa.Column("platform", sa.String(length=64), nullable=True),
        sa.Column("app_version", sa.String(length=64), nullable=True),
        sa.Column("account_deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("document IN ('terms', 'privacy')", name="ck_user_consents_document"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_user_consents_account_deleted_at", "user_consents", ["account_deleted_at"], unique=False)
    op.create_index("ix_user_consents_email_hash", "user_consents", ["email_hash"], unique=False)
    op.create_index("ix_user_consents_user_id", "user_consents", ["user_id"], unique=False)
    op.add_column("users", sa.Column("accepted_terms_version", sa.String(length=10), nullable=True))
    op.add_column("users", sa.Column("accepted_privacy_version", sa.String(length=10), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "accepted_privacy_version")
    op.drop_column("users", "accepted_terms_version")
    op.drop_index("ix_user_consents_user_id", table_name="user_consents")
    op.drop_index("ix_user_consents_email_hash", table_name="user_consents")
    op.drop_index("ix_user_consents_account_deleted_at", table_name="user_consents")
    op.drop_table("user_consents")
