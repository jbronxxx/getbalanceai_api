"""Шифрование имени и email пользователей (SEC-10).

* `users.email` и `users.name` — `TEXT` с шифртекстом AES-256-GCM (`app/pii.py`), существующие строки шифруются
  на месте ключом из `PII_ENCRYPTION_KEYS`.
* `users.email_hash` — слепой индекс HMAC(email), уникальный: по нему ищется пользователь при входе. Прежнее
  ограничение уникальности открытого email (`users_email_key`) снимается.

Ключи берутся из окружения, как у приложения: без них (в production) миграция не стартует — деплой
останавливается до перезапуска API. Адреса, совпадающие без учёта регистра, дают одинаковый индекс:
такая база миграцию не пройдёт (откат всей транзакции), пару нужно разобрать вручную.

`downgrade()` расшифровывает строки обратно и возвращает прежние колонки — нужны те же ключи.

Revision ID: 11c4d089e0f6
Revises: t01901901901
Create Date: 2026-11-11 18:06:41.762599

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from app.pii import get_pii_cipher

# revision identifiers, used by Alembic.
revision: str = "11c4d089e0f6"
down_revision: Union[str, None] = "t01901901901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EMAIL_CONTEXT = "users.email"
NAME_CONTEXT = "users.name"


def upgrade() -> None:
    op.add_column("users", sa.Column("email_hash", sa.String(length=64), nullable=True))
    op.drop_constraint("users_email_key", "users", type_="unique")
    op.alter_column("users", "email", type_=sa.Text(), existing_type=sa.String(length=255), existing_nullable=False)
    op.alter_column("users", "name", type_=sa.Text(), existing_type=sa.String(length=100), existing_nullable=False)

    cipher = get_pii_cipher()
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email, name FROM users")).all()
    if rows:
        # Один executemany на все строки: пользователей тысячи, запрос на строку растянул бы миграцию
        bind.execute(
            sa.text("UPDATE users SET email = :email, name = :name, email_hash = :email_hash WHERE id = :id"),
            [
                {
                    "id": user_id,
                    "email": cipher.encrypt(email, EMAIL_CONTEXT),
                    "name": cipher.encrypt(name, NAME_CONTEXT),
                    "email_hash": cipher.blind_index(email),
                }
                for user_id, email, name in rows
            ],
        )

    op.alter_column("users", "email_hash", existing_type=sa.String(length=64), nullable=False)
    op.create_unique_constraint("users_email_hash_key", "users", ["email_hash"])


def downgrade() -> None:
    cipher = get_pii_cipher()
    bind = op.get_bind()
    rows = bind.execute(sa.text("SELECT id, email, name FROM users")).all()
    if rows:
        bind.execute(
            sa.text("UPDATE users SET email = :email, name = :name WHERE id = :id"),
            [
                {"id": user_id, "email": cipher.decrypt(email, EMAIL_CONTEXT), "name": cipher.decrypt(name, NAME_CONTEXT)}
                for user_id, email, name in rows
            ],
        )

    op.drop_constraint("users_email_hash_key", "users", type_="unique")
    op.drop_column("users", "email_hash")
    op.alter_column("users", "name", type_=sa.String(length=100), existing_type=sa.Text(), existing_nullable=False)
    op.alter_column("users", "email", type_=sa.String(length=255), existing_type=sa.Text(), existing_nullable=False)
    op.create_unique_constraint("users_email_key", "users", ["email"])
