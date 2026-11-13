"""Тесты шифрования имени и email пользователя (SEC-10): шифр, хранение в БД, поиск, API, журналы, ротация ключей."""

import base64
import logging
import uuid

import pytest
from sqlalchemy import Text, select, type_coerce
from sqlalchemy.exc import StatementError

from app.models.models import User, UserConsent
from app.pii import (
    PiiCipher,
    PiiDecryptError,
    PiiKeyError,
    cipher_from_settings,
    get_pii_cipher,
    parse_encryption_keys,
)
from app.services.auth_service import AuthService
from config_reader.config_reader import config
from scripts.rotate_pii_keys import rotate_pii_keys
from tests.conftest import TestingSessionLocal


def _key(fill: bytes) -> str:
    return base64.b64encode(fill * 32).decode()


def _cipher(keys: list[str], hmac_fill: bytes = b"h") -> PiiCipher:
    return cipher_from_settings(keys, _key(hmac_fill))


@pytest.fixture
def use_pii_keys(monkeypatch: pytest.MonkeyPatch):
    """Подменить ключи в конфигурации и сбросить закэшированный шифр (и вернуть его после теста)."""

    def apply(keys: list[str], hmac_fill: bytes) -> None:
        monkeypatch.setattr(config, "pii_encryption_keys", keys)
        monkeypatch.setattr(config, "pii_hmac_key", _key(hmac_fill))
        get_pii_cipher.cache_clear()

    yield apply
    get_pii_cipher.cache_clear()


async def _raw_user(user_id: uuid.UUID) -> tuple[str, str, str]:
    """Значения колонок как они лежат в БД: `type_coerce` к Text отключает расшифровку."""
    async with TestingSessionLocal() as session:
        row = (
            await session.execute(
                select(type_coerce(User.email, Text()), type_coerce(User.name, Text()), User.email_hash).where(
                    User.id == user_id
                )
            )
        ).one()
        return row[0], row[1], row[2]


class TestPiiCipher:
    """AES-256-GCM и слепой индекс без БД."""

    def test_roundtrip_with_random_nonce_and_key_id_prefix(self):
        cipher = _cipher(["k1:" + _key(b"a")])
        first = cipher.encrypt("Анна Смирнова", "users.name")
        second = cipher.encrypt("Анна Смирнова", "users.name")

        assert first.startswith("k1:") and second.startswith("k1:")
        assert first != second  # случайный nonce: одинаковые имена не видны в БД как одинаковые
        assert "Анна" not in first
        assert cipher.decrypt(first, "users.name") == "Анна Смирнова"

    def test_ciphertext_is_bound_to_column(self):
        """Шифртекст email не расшифровывается как имя: имя колонки входит в AAD."""
        cipher = _cipher(["k1:" + _key(b"a")])
        token = cipher.encrypt("user@example.com", "users.email")

        with pytest.raises(PiiDecryptError):
            cipher.decrypt(token, "users.name")

    @pytest.mark.parametrize("mutate", ["unknown_key", "tampered", "no_prefix", "bad_base64"])
    def test_broken_values_raise(self, mutate: str):
        cipher = _cipher(["k1:" + _key(b"a")])
        token = cipher.encrypt("user@example.com", "users.email")
        payload = token.partition(":")[2]
        broken = {
            "unknown_key": "k9:" + payload,
            "tampered": "k1:" + payload[:-4] + ("AAAA" if not payload.endswith("AAAA") else "BBBB"),
            "no_prefix": payload,
            "bad_base64": "k1:###",
        }[mutate]

        with pytest.raises(PiiDecryptError):
            cipher.decrypt(broken, "users.email")

    def test_old_key_still_decrypts_after_rotation(self):
        """Новый ключ первым — им шифруются новые значения; старые строки читаются старым ключом."""
        old = _cipher(["k1:" + _key(b"a")])
        token = old.encrypt("user@example.com", "users.email")
        rotated = _cipher(["k2:" + _key(b"b"), "k1:" + _key(b"a")])

        assert rotated.decrypt(token, "users.email") == "user@example.com"
        assert rotated.encrypt("x", "users.email").startswith("k2:")
        assert rotated.key_id_of(token) == "k1"

    def test_blind_index_ignores_case_and_spaces_and_depends_on_key(self):
        cipher = _cipher(["k1:" + _key(b"a")])

        assert cipher.blind_index(" User@Example.COM ") == cipher.blind_index("user@example.com")
        assert len(cipher.blind_index("user@example.com")) == 64
        assert _cipher(["k1:" + _key(b"a")], b"z").blind_index("user@example.com") != cipher.blind_index("user@example.com")

    @pytest.mark.parametrize(
        "entries",
        [
            [],
            ["k1"],
            ["bad id:" + _key(b"a")],
            ["k1:not-base64!"],
            ["k1:" + base64.b64encode(b"short").decode()],
            ["k1:" + _key(b"a"), "k1:" + _key(b"b")],
        ],
    )
    def test_invalid_key_settings_rejected(self, entries: list[str]):
        with pytest.raises(PiiKeyError):
            parse_encryption_keys(entries)

    def test_invalid_hmac_key_rejected(self):
        with pytest.raises(PiiKeyError):
            cipher_from_settings(["k1:" + _key(b"a")], "short")


class TestEncryptedUserColumns:
    """Хранение имени и email в БД и поиск по слепому индексу."""

    @pytest.mark.asyncio
    async def test_name_and_email_stored_encrypted_with_blind_index(self, test_user: User):
        email, name, email_hash = await _raw_user(test_user.id)

        assert test_user.email not in email and "@" not in email
        assert test_user.name not in name
        assert email.startswith(get_pii_cipher().current_key_id + ":")
        assert email_hash == get_pii_cipher().blind_index(test_user.email)

    @pytest.mark.asyncio
    async def test_found_by_email_in_any_case(self, db_session, test_user: User):
        found = await AuthService(db_session).find_user_by_email(f"  {test_user.email.upper()} ")

        assert found is not None and found.id == test_user.id
        assert await AuthService(db_session).find_user_by_email("missing@example.com") is None

    @pytest.mark.asyncio
    async def test_changing_email_updates_blind_index(self, db_session, test_user: User):
        test_user.email = "renamed@example.com"
        await db_session.flush()

        assert test_user.email_hash == get_pii_cipher().blind_index("renamed@example.com")
        assert (await db_session.execute(select(User.id).where(User.email_matches("RENAMED@example.com")))).scalar_one()

    def test_sql_comparison_of_encrypted_column_is_forbidden(self):
        """Сравнение шифртекста в SQL молча ничего бы не находило — поэтому ошибка сразу."""
        with pytest.raises(TypeError, match="слепому индексу"):
            select(User).where(User.email == "user@example.com")
        with pytest.raises(TypeError):
            select(User).where(User.name.like("%a%"))

    @pytest.mark.asyncio
    async def test_plaintext_length_limit_kept(self, db_session):
        """Длина открытого значения ограничена, как у прежних String(255) / String(100)."""
        db_session.add(User(email="long@example.com", name="Очень длинное имя " * 6))
        with pytest.raises(StatementError, match="длиннее 100") as error:
            await db_session.flush()
        await db_session.rollback()
        # Текст ошибки уходит в журнал: параметры запроса (открытые имя и email) в нём скрыты
        assert "long@example.com" not in str(error.value)
        assert "Очень длинное" not in str(error.value)


class TestPiiInApiAndLogs:
    """Клиент видит открытые значения, журналы — нет."""

    @pytest.mark.asyncio
    async def test_profile_returns_plaintext_and_stores_new_name_encrypted(
        self, client, auth_headers: dict[str, str], test_user: User
    ):
        user_id, email = test_user.id, test_user.email

        response = await client.patch("/api/v1/auth/me", json={"name": "Мария Кузнецова"}, headers=auth_headers)
        assert response.status_code == 200
        assert response.json()["data"]["name"] == "Мария Кузнецова"

        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]
        assert (me["email"], me["name"]) == (email, "Мария Кузнецова")
        _, raw_name, _ = await _raw_user(user_id)
        assert "Мария" not in raw_name

    @pytest.mark.asyncio
    async def test_oauth_login_logs_neither_email_nor_name(self, db_session, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG, logger="app"):
            await AuthService(db_session)._handle_oauth_user("google", "Ivan.Petrov@Example.com", "Иван Петров", None, {})
            await AuthService(db_session)._handle_oauth_user("google", "ivan.petrov@example.com", "Иван Петров", None, {})

        logs = "\n".join(record.getMessage() for record in caplog.records if record.name.startswith("app"))
        assert "Зарегистрирован новый пользователь" in logs and "успешно авторизован" in logs
        assert "ivan.petrov" not in logs.lower()
        assert "Иван" not in logs


class TestRotatePiiKeys:
    """Скрипт ротации: перешифровка текущим ключом и пересчёт слепого индекса."""

    @pytest.mark.asyncio
    async def test_rotation_reencrypts_and_reindexes(self, use_pii_keys):
        use_pii_keys(["old:" + _key(b"o")], b"1")
        user_id = uuid.uuid4()
        async with TestingSessionLocal() as session:
            user = User(id=user_id, email="rotate@example.com", name="Ротация")
            session.add(user)
            await session.flush()
            old_hash = user.email_hash
            # Согласие живого аккаунта и согласие удалённого (user_id пуст, email неизвестен)
            session.add(UserConsent(user_id=user_id, email_hash=old_hash, document="terms", version="2026-11-13"))
            session.add(UserConsent(user_id=None, email_hash="deleted-hash", document="terms", version="2026-11-13"))
            await session.commit()
        assert (await _raw_user(user_id))[0].startswith("old:")

        use_pii_keys(["new:" + _key(b"n"), "old:" + _key(b"o")], b"2")
        assert await rotate_pii_keys(session_factory=TestingSessionLocal) >= 1

        email, name, email_hash = await _raw_user(user_id)
        assert email.startswith("new:") and name.startswith("new:")
        assert email_hash == get_pii_cipher().blind_index("rotate@example.com")
        async with TestingSessionLocal() as session:
            hashes = (await session.execute(select(UserConsent.user_id, UserConsent.email_hash))).all()
            assert {row.user_id: row.email_hash for row in hashes} == {user_id: email_hash, None: "deleted-hash"}
            await session.execute(UserConsent.__table__.delete())
            await session.commit()

        # Старый ключ больше не нужен: строки читаются и находятся только с новыми ключами
        use_pii_keys(["new:" + _key(b"n")], b"2")
        async with TestingSessionLocal() as session:
            user = (await session.execute(select(User).where(User.email_matches("ROTATE@example.com")))).scalar_one()
            assert (user.id, user.email, user.name) == (user_id, "rotate@example.com", "Ротация")
            await session.delete(user)
            await session.commit()
