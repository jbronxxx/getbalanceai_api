"""Тесты проверки ID-токенов Google и Apple на реальной RSA-подписи.

JWKS провайдера подменяется ключом, сгенерированным в тесте; jwt.decode не мокается,
поэтому проверяются подпись, срок, aud и iss так же, как на проде.
"""

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import ErrorCode, UnauthorizedException
from app.models.models import User
from app.schemas.schemas import AppleSignInRequest, GoogleSignInRequest
from app.services.auth_service import AuthService
from config_reader.config_reader import config

GOOGLE_ISS = "https://accounts.google.com"
APPLE_ISS = "https://appleid.apple.com"

PROVIDER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
FOREIGN_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeJWKSClient:
    """Отдаёт публичный ключ провайдера вместо запроса JWKS по сети."""

    def get_signing_key_from_jwt(self, token: str) -> SimpleNamespace:
        return SimpleNamespace(key=PROVIDER_KEY.public_key())


@pytest.fixture(autouse=True)
def fake_jwks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(AuthService, "_google_jwks_client", _FakeJWKSClient())
    monkeypatch.setattr(AuthService, "_apple_jwks_client", _FakeJWKSClient())


def _id_token(*, signing_key=PROVIDER_KEY, drop: tuple[str, ...] = (), **claims) -> str:
    now = int(time.time())
    payload = {"sub": "provider-user-1", "iat": now, "exp": now + 600, **claims}
    for claim in drop:
        payload.pop(claim, None)
    return jwt.encode(payload, signing_key, algorithm="RS256")


def _google_token(**overrides) -> str:
    claims = {
        "iss": GOOGLE_ISS,
        "aud": "test-google-android",
        "email": "google_verified@example.com",
        "email_verified": True,
    }
    claims.update(overrides)
    return _id_token(**claims)


def _apple_token(**overrides) -> str:
    claims = {"iss": APPLE_ISS, "aud": "test.apple.bundle", "email": "apple_verified@example.com"}
    claims.update(overrides)
    return _id_token(**claims)


class TestGoogleTokenVerification:
    """Проверка ID-токена Google."""

    @pytest.mark.asyncio
    async def test_valid_token_for_second_client_id_succeeds(self, db_session: AsyncSession):
        """Токен для любого из настроенных client ID (здесь iOS) принимается."""
        tokens = await AuthService(db_session).google_login(
            GoogleSignInRequest(id_token=_google_token(aud="test-google-ios"))
        )
        assert tokens.user.email == "google_verified@example.com"

    @pytest.mark.asyncio
    async def test_short_issuer_form_accepted(self, db_session: AsyncSession):
        """Google выпускает токены и с iss без схемы — accounts.google.com."""
        tokens = await AuthService(db_session).google_login(
            GoogleSignInRequest(id_token=_google_token(iss="accounts.google.com"))
        )
        assert tokens.access_token

    @pytest.mark.asyncio
    async def test_email_verified_string_true_accepted(self, db_session: AsyncSession):
        """email_verified строкой "true" считается подтверждённым."""
        payload = GoogleSignInRequest(id_token=_google_token(email_verified="true"))
        tokens = await AuthService(db_session).google_login(payload)
        assert tokens.access_token

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "token_kwargs",
        [
            pytest.param({"aud": "someone-elses-app.apps.googleusercontent.com"}, id="foreign-aud"),
            pytest.param({"aud": ["test-google-android-evil"]}, id="aud-prefix-match"),
            pytest.param({"iss": "https://evil.example.com"}, id="wrong-iss"),
            pytest.param({"iss": APPLE_ISS}, id="apple-iss"),
            pytest.param({"email_verified": False}, id="email-not-verified"),
            pytest.param({"email_verified": "false"}, id="email-not-verified-string"),
            pytest.param({"drop": ("email_verified",)}, id="email-verified-missing"),
            pytest.param({"drop": ("aud",)}, id="aud-missing"),
            pytest.param({"drop": ("iss",)}, id="iss-missing"),
            pytest.param({"drop": ("exp",)}, id="exp-missing"),
            pytest.param({"exp": int(time.time()) - 60}, id="expired"),
            pytest.param({"signing_key": FOREIGN_KEY}, id="foreign-signature"),
        ],
    )
    async def test_invalid_token_rejected(self, db_session: AsyncSession, token_kwargs: dict):
        """Токен с чужим aud, неверным iss, неподтверждённым email, без обязательных claim или чужой подписью — 401."""
        with pytest.raises(UnauthorizedException) as exc_info:
            await AuthService(db_session).google_login(GoogleSignInRequest(id_token=_google_token(**token_kwargs)))
        assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS

        result = await db_session.execute(select(User).where(User.email == "google_verified@example.com"))
        assert result.scalar_one_or_none() is None

    @pytest.mark.asyncio
    async def test_rejected_when_client_ids_not_configured(self, db_session: AsyncSession, monkeypatch):
        """Без настроенного GOOGLE_CLIENT_ID вход отклоняется, а не принимает токен любого приложения."""
        monkeypatch.setattr(config, "google_client_ids", [])
        with pytest.raises(UnauthorizedException):
            await AuthService(db_session).google_login(GoogleSignInRequest(id_token=_google_token()))

    @pytest.mark.asyncio
    async def test_endpoint_rejects_foreign_audience(self, client: AsyncClient):
        """POST /api/v1/auth/google с токеном чужого приложения — 401 INVALID_CREDENTIALS."""
        response = await client.post(
            "/api/v1/auth/google", json={"id_token": _google_token(aud="someone-elses-app.apps.googleusercontent.com")}
        )
        assert response.status_code == 401
        data = response.json()
        assert data["status"] == "error"
        assert data["code"] == ErrorCode.INVALID_CREDENTIALS

    @pytest.mark.asyncio
    async def test_endpoint_accepts_valid_token(self, client: AsyncClient):
        """POST /api/v1/auth/google с корректным токеном — 200 и пара токенов."""
        response = await client.post("/api/v1/auth/google", json={"id_token": _google_token()})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["user"]["email"] == "google_verified@example.com"


class TestAppleTokenVerification:
    """Проверка ID-токена Apple."""

    @pytest.mark.asyncio
    async def test_valid_token_succeeds(self, db_session: AsyncSession):
        tokens = await AuthService(db_session).apple_login(AppleSignInRequest(identity_token=_apple_token()))
        assert tokens.user.email == "apple_verified@example.com"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "token_kwargs",
        [
            pytest.param({"aud": "com.someone.else"}, id="foreign-aud"),
            pytest.param({"iss": GOOGLE_ISS}, id="google-iss"),
            pytest.param({"drop": ("iss",)}, id="iss-missing"),
            pytest.param({"drop": ("aud",)}, id="aud-missing"),
            pytest.param({"exp": int(time.time()) - 60}, id="expired"),
            pytest.param({"signing_key": FOREIGN_KEY}, id="foreign-signature"),
        ],
    )
    async def test_invalid_token_rejected(self, db_session: AsyncSession, token_kwargs: dict):
        """Токен с чужим aud, неверным iss, без обязательных claim или чужой подписью — 401."""
        with pytest.raises(UnauthorizedException) as exc_info:
            await AuthService(db_session).apple_login(AppleSignInRequest(identity_token=_apple_token(**token_kwargs)))
        assert exc_info.value.code == ErrorCode.INVALID_CREDENTIALS

    @pytest.mark.asyncio
    async def test_rejected_when_client_ids_not_configured(self, db_session: AsyncSession, monkeypatch):
        """Без настроенного APPLE_CLIENT_ID вход отклоняется."""
        monkeypatch.setattr(config, "apple_client_ids", [])
        with pytest.raises(UnauthorizedException):
            await AuthService(db_session).apple_login(AppleSignInRequest(identity_token=_apple_token()))
