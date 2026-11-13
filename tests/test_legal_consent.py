"""Принятие юридических документов (LEG-01): вход с отметкой, проверка доступа, повторное принятие, хранение."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode
from app.legal import LEGAL_HISTORY, LegalVersion, current_version
from app.models.models import User, UserConsent
from app.services.auth_service import AuthService
from app.services.consent_service import CONSENT_RETENTION_DAYS, ConsentService
from app.tasks.token_cleanup import run_consent_cleanup
from tests.conftest import TestingSessionLocal

TERMS = current_version("terms").version
PRIVACY = current_version("privacy").version
CLIENT_HEADERS = {"X-App-Language": "ru", "X-Platform": "Android 15; API 35", "X-App-Version": "1.4.0+42"}
GOOGLE_CLAIMS = {
    "email": "consent_user@example.com",
    "iss": "https://accounts.google.com",
    "email_verified": True,
    "name": "Consent User",
}


async def _consents(db: AsyncSession, **filters) -> list[UserConsent]:
    query = select(UserConsent).filter_by(**filters).order_by(UserConsent.document)
    return list((await db.execute(query)).scalars().all())


async def _user_without_consent(db: AsyncSession) -> tuple[uuid.UUID, dict[str, str]]:
    """Пользователь, который ещё не принял текущие версии (существовал до LEG-01 или вышла новая версия)."""
    user = User(email=f"legacy_{uuid.uuid4().hex[:8]}@example.com", name="Legacy")
    db.add(user)
    await db.commit()
    await db.refresh(user)
    token = AuthService(db).create_access_token(user.id)
    return user.id, {"Authorization": f"Bearer {token}"}


async def _google_login(client: AsyncClient, **extra) -> dict:
    with (
        patch("app.services.auth_service.PyJWKClient"),
        patch("app.services.auth_service.jwt.decode", return_value=GOOGLE_CLAIMS),
    ):
        return await client.post(
            "/api/v1/auth/google", json={"id_token": "t", "accept_legal": True, **extra}, headers=CLIENT_HEADERS
        )


class TestLoginAcceptance:
    """Вход возможен только с отметкой; отметка записывает принятие текущих версий."""

    @pytest.mark.asyncio
    async def test_login_records_both_documents_with_client_data(self, client: AsyncClient, db_session: AsyncSession):
        response = await _google_login(client)
        assert response.status_code == 200
        legal = response.json()["data"]["user"]["legal"]
        assert legal == {
            "terms_version": TERMS,
            "terms_accepted": True,
            "privacy_version": PRIVACY,
            "privacy_accepted": True,
        }

        user = (await db_session.execute(select(User).where(User.email_matches(GOOGLE_CLAIMS["email"])))).scalar_one()
        assert (user.accepted_terms_version, user.accepted_privacy_version) == (TERMS, PRIVACY)
        consents = await _consents(db_session, user_id=user.id)
        assert [(c.document, c.version) for c in consents] == [("privacy", PRIVACY), ("terms", TERMS)]
        for consent in consents:
            assert consent.email_hash == user.email_hash
            assert (consent.language, consent.platform, consent.app_version) == ("ru", "Android 15; API 35", "1.4.0+42")
            assert consent.account_deleted_at is None

    @pytest.mark.asyncio
    async def test_repeat_login_does_not_duplicate_history(self, client: AsyncClient, db_session: AsyncSession):
        await _google_login(client)
        await _google_login(client)
        assert len(await _consents(db_session)) == 2

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [{"id_token": "t"}, {"id_token": "t", "accept_legal": False}])
    async def test_login_without_acceptance_is_rejected(self, client: AsyncClient, payload: dict):
        response = await client.post("/api/v1/auth/google", json=payload)
        assert response.status_code == 422
        assert response.json()["code"] == ErrorCode.VALIDATION_ERROR

    @pytest.mark.asyncio
    async def test_apple_login_without_acceptance_is_rejected(self, client: AsyncClient):
        response = await client.post("/api/v1/auth/apple", json={"identity_token": "t"})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_existing_user_accepts_new_version_on_login(self, client: AsyncClient, db_session: AsyncSession):
        """Аккаунт с устаревшей версией: вход с отметкой принимает текущую и добавляет запись к истории."""
        user = User(email=GOOGLE_CLAIMS["email"], name="Old", accepted_terms_version="2026-01-01")
        db_session.add(user)
        await db_session.commit()

        assert (await _google_login(client)).status_code == 200
        await db_session.refresh(user)
        assert user.accepted_terms_version == TERMS
        assert {c.document for c in await _consents(db_session, user_id=user.id)} == {"terms", "privacy"}

    @pytest.mark.asyncio
    async def test_long_client_headers_fit_columns(self, db_session: AsyncSession):
        user = User(email="long_headers@example.com", name="Long")
        db_session.add(user)
        await db_session.flush()
        await ConsentService(db_session).accept_current(user, {"platform": "x" * 65, "app_version": "1" * 65})
        consent = (await _consents(db_session, user_id=user.id))[0]
        assert len(consent.platform) == 64 and len(consent.app_version) == 64
        assert consent.language is None


class TestAccessGate:
    """Без принятой текущей версии соглашения данные закрыты; профиль, принятие, выход и удаление — открыты."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("method", "path", "body"),
        [
            ("GET", "/api/v1/transactions/", None),
            ("GET", "/api/v1/budgets/", None),
            ("GET", "/api/v1/recurring-payments/", None),
            ("GET", "/api/v1/insights/", None),
            ("POST", "/api/v1/sync/", {"transactions": []}),
            ("POST", "/api/v1/voice/parse", {"text": "кофе 3"}),
            ("PATCH", "/api/v1/auth/me", {"name": "New"}),
        ],
    )
    async def test_data_endpoints_require_current_terms(
        self, client: AsyncClient, db_session: AsyncSession, method: str, path: str, body: dict | None
    ):
        _, headers = await _user_without_consent(db_session)
        response = await client.request(method, path, json=body, headers=headers)
        assert response.status_code == 403
        assert response.json()["code"] == ErrorCode.TERMS_NOT_ACCEPTED

    @pytest.mark.asyncio
    async def test_profile_shows_pending_acceptance(self, client: AsyncClient, db_session: AsyncSession):
        _, headers = await _user_without_consent(db_session)
        response = await client.get("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        data = response.json()["data"]
        assert data["legal"] == {
            "terms_version": TERMS,
            "terms_accepted": False,
            "privacy_version": PRIVACY,
            "privacy_accepted": False,
        }
        assert "accepted_terms_version" not in data

    @pytest.mark.asyncio
    async def test_account_can_be_deleted_without_acceptance(self, client: AsyncClient, db_session: AsyncSession):
        user_id, headers = await _user_without_consent(db_session)
        response = await client.delete("/api/v1/auth/me", headers=headers)
        assert response.status_code == 200
        assert await db_session.get(User, user_id) is None

    @pytest.mark.asyncio
    async def test_logout_without_acceptance(self, client: AsyncClient, db_session: AsyncSession):
        _, headers = await _user_without_consent(db_session)
        assert (await client.post("/api/v1/auth/logout", headers=headers)).status_code == 200

    @pytest.mark.asyncio
    async def test_new_terms_version_closes_access(
        self, client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ):
        """Выход новой версии соглашения закрывает данные пользователю, принявшему прежнюю."""
        assert (await client.get("/api/v1/transactions/", headers=auth_headers)).status_code == 200
        newer = LegalVersion(effective=datetime(2027, 1, 1).date(), changes={"ru": ["-"], "en": ["-"]})
        monkeypatch.setitem(LEGAL_HISTORY, "terms", (*LEGAL_HISTORY["terms"], newer))
        response = await client.get("/api/v1/transactions/", headers=auth_headers)
        assert response.status_code == 403
        assert response.json()["code"] == ErrorCode.TERMS_NOT_ACCEPTED

    @pytest.mark.asyncio
    async def test_new_privacy_version_does_not_close_access(
        self, client: AsyncClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
    ):
        """Новая версия политики — только уведомление: данные доступны, в профиле privacy_accepted=false."""
        newer = LegalVersion(effective=datetime(2027, 1, 1).date(), changes={"ru": ["-"], "en": ["-"]})
        monkeypatch.setitem(LEGAL_HISTORY, "privacy", (*LEGAL_HISTORY["privacy"], newer))
        assert (await client.get("/api/v1/transactions/", headers=auth_headers)).status_code == 200
        legal = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()["data"]["legal"]
        assert legal["privacy_accepted"] is False and legal["privacy_version"] == "2027-01-01"
        assert legal["terms_accepted"] is True


class TestLegalVersionsEndpoint:
    """GET /api/v1/legal — текущие версии и «Что изменилось» на языке интерфейса, без авторизации."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("params", "headers", "lang"),
        [
            ({"lang": "ru"}, {"X-App-Language": "en"}, "ru"),
            ({}, {"X-App-Language": "ru", "Accept-Language": "en"}, "ru"),
            ({}, {"Accept-Language": "ru-RU,ru;q=0.9"}, "ru"),
            ({}, {"X-App-Language": "de"}, "en"),
            ({}, {}, "en"),
        ],
    )
    async def test_versions_in_interface_language(self, client: AsyncClient, params: dict, headers: dict, lang: str):
        response = await client.get("/api/v1/legal", params=params, headers=headers)
        assert response.status_code == 200
        data = response.json()["data"]
        for document in ("terms", "privacy"):
            version = current_version(document)
            assert data[document] == {
                "version": version.version,
                "effective_date": version.version,
                "changes": version.changes[lang],
            }


class TestAcceptEndpoint:
    """POST /api/v1/legal/accept — принятие показанных версий."""

    @pytest.mark.asyncio
    async def test_accept_current_terms_opens_access(self, client: AsyncClient, db_session: AsyncSession):
        user_id, headers = await _user_without_consent(db_session)
        response = await client.post(
            "/api/v1/legal/accept",
            json={"terms_version": TERMS, "privacy_version": PRIVACY},
            headers=headers | CLIENT_HEADERS,
        )
        assert response.status_code == 200
        assert response.json()["data"]["legal"]["terms_accepted"] is True
        assert (await client.get("/api/v1/transactions/", headers=headers)).status_code == 200
        consents = await _consents(db_session, user_id=user_id)
        assert [(c.document, c.version, c.language) for c in consents] == [
            ("privacy", PRIVACY, "ru"),
            ("terms", TERMS, "ru"),
        ]

    @pytest.mark.asyncio
    async def test_accept_privacy_only(self, client: AsyncClient, db_session: AsyncSession):
        user_id, headers = await _user_without_consent(db_session)
        response = await client.post("/api/v1/legal/accept", json={"privacy_version": PRIVACY}, headers=headers)
        legal = response.json()["data"]["legal"]
        assert (legal["privacy_accepted"], legal["terms_accepted"]) == (True, False)
        assert [c.document for c in await _consents(db_session, user_id=user_id)] == ["privacy"]

    @pytest.mark.asyncio
    async def test_outdated_version_conflict(self, client: AsyncClient, db_session: AsyncSession):
        user_id, headers = await _user_without_consent(db_session)
        response = await client.post("/api/v1/legal/accept", json={"terms_version": "2026-01-01"}, headers=headers)
        assert response.status_code == 409
        body = response.json()
        assert body["code"] == ErrorCode.LEGAL_VERSION_OUTDATED
        assert body["details"] == {"document": "terms", "current_version": TERMS}
        assert await _consents(db_session, user_id=user_id) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("payload", [{}, {"terms_version": "latest"}, {"terms_version": None, "privacy_version": None}])
    async def test_invalid_payload(self, client: AsyncClient, auth_headers: dict[str, str], payload: dict):
        response = await client.post("/api/v1/legal/accept", json=payload, headers=auth_headers)
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_accept_requires_auth(self, client: AsyncClient):
        response = await client.post("/api/v1/legal/accept", json={"terms_version": TERMS})
        assert response.status_code in (401, 403)

    @pytest.mark.asyncio
    async def test_repeat_accept_keeps_history(self, client: AsyncClient, db_session: AsyncSession):
        user_id, headers = await _user_without_consent(db_session)
        for _ in range(2):
            await client.post("/api/v1/legal/accept", json={"terms_version": TERMS}, headers=headers)
        assert len(await _consents(db_session, user_id=user_id)) == 1


class TestRetention:
    """Удаление аккаунта сохраняет согласия на срок хранения; очистка удаляет просроченные."""

    @pytest.mark.asyncio
    async def test_account_deletion_keeps_consents_without_user(self, client: AsyncClient, db_session: AsyncSession):
        await _google_login(client)
        user = (await db_session.execute(select(User).where(User.email_matches(GOOGLE_CLAIMS["email"])))).scalar_one()
        user_id, email_hash = user.id, user.email_hash
        await AuthService(db_session).delete_account(user_id)
        await db_session.commit()
        db_session.expire_all()

        consents = await _consents(db_session, email_hash=email_hash)
        assert len(consents) == 2
        for consent in consents:
            assert consent.user_id is None
            assert consent.account_deleted_at is not None

    @pytest.mark.asyncio
    async def test_cleanup_removes_only_expired(self, db_session: AsyncSession):
        now = datetime.now(timezone.utc)
        living = User(email="living@example.com", name="Living")
        db_session.add(living)
        await db_session.flush()
        living_hash = living.email_hash
        expired_at = now - timedelta(days=CONSENT_RETENTION_DAYS + 1)
        recent_at = now - timedelta(days=CONSENT_RETENTION_DAYS - 1)
        db_session.add_all(
            [
                UserConsent(email_hash="old", document="terms", version=TERMS, account_deleted_at=expired_at),
                UserConsent(email_hash="recent", document="terms", version=TERMS, account_deleted_at=recent_at),
                UserConsent(user_id=living.id, email_hash=living_hash, document="terms", version=TERMS),
            ]
        )
        await db_session.commit()

        assert await run_consent_cleanup(session_factory=TestingSessionLocal) == 1
        db_session.expire_all()
        assert {c.email_hash for c in await _consents(db_session)} == {"recent", living_hash}
