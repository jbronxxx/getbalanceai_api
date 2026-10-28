"""Юнит и интеграционные тесты для AuthService и эндпоинтов авторизации."""

import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import BadRequestException, NotFoundException, UnauthorizedException
from app.models.models import Token, User
from app.schemas.schemas import (
    AppleSignInRequest,
    GoogleSignInRequest,
    UserLogin,
    UserRegister,
)
from app.services.auth_service import AuthService, get_current_user
from config_reader.config_reader import config


class TestAuthServiceUnit:
    """Юнит-тесты бизнес-логики AuthService."""

    @pytest.mark.asyncio
    async def test_register_success(self, db_session: AsyncSession):
        """Успешная регистрация нового пользователя."""
        service = AuthService(db_session)
        payload = UserRegister(
            email=f"newuser_{uuid.uuid4().hex[:6]}@example.com",
            password="SecurePassword123!",
            name="New User",
        )
        user = await service.register(payload)

        assert user.id is not None
        assert user.email == payload.email
        assert user.name == payload.name
        assert user.hashed_password != payload.password

    @pytest.mark.asyncio
    async def test_register_duplicate_email(self, db_session: AsyncSession, test_user: User):
        """Регистрация с существующим email вызывает BadRequestException."""
        service = AuthService(db_session)
        payload = UserRegister(
            email=test_user.email,
            password="AnotherPassword123!",
            name="Duplicate User",
        )
        with pytest.raises(BadRequestException) as exc_info:
            await service.register(payload)
        assert exc_info.value.code == "USER_ALREADY_EXISTS"

    @pytest.mark.asyncio
    async def test_login_success(self, db_session: AsyncSession):
        """Успешный вход пользователя с валидными учетными данными."""
        service = AuthService(db_session)
        email = f"logintest_{uuid.uuid4().hex[:6]}@example.com"
        password = "ValidPassword123!"
        await service.register(UserRegister(email=email, password=password, name="Login User"))

        tokens = await service.login(UserLogin(email=email, password=password))
        assert tokens.access_token is not None
        assert tokens.refresh_token is not None
        assert tokens.token_type == "bearer"

    @pytest.mark.asyncio
    async def test_login_invalid_password(self, db_session: AsyncSession, test_user: User):
        """Попытка входа с неверным паролем вызывает UnauthorizedException."""
        service = AuthService(db_session)
        with pytest.raises(UnauthorizedException) as exc_info:
            await service.login(UserLogin(email=test_user.email, password="WrongPassword!"))
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    @pytest.mark.asyncio
    async def test_login_nonexistent_user(self, db_session: AsyncSession):
        """Попытка входа с несуществующим email вызывает UnauthorizedException."""
        service = AuthService(db_session)
        with pytest.raises(UnauthorizedException) as exc_info:
            await service.login(UserLogin(email="nonexistent@example.com", password="Password123!"))
        assert exc_info.value.code == "INVALID_CREDENTIALS"

    @pytest.mark.asyncio
    async def test_refresh_tokens_success(self, db_session: AsyncSession, test_user: User):
        """Успешная ротация токенов по refresh-токену."""
        service = AuthService(db_session)
        refresh_token = await service.create_refresh_token(test_user.id)

        new_tokens = await service.refresh_tokens(refresh_token)
        assert new_tokens.access_token is not None
        assert new_tokens.refresh_token is not None
        assert new_tokens.refresh_token != refresh_token

        # Старый refresh токен должен быть revoked
        import hashlib

        hashed = hashlib.sha256(refresh_token.encode()).hexdigest()
        result = await db_session.execute(select(Token).where(Token.token_hash == hashed))
        old_token = result.scalar_one_or_none()
        assert old_token is not None
        assert old_token.status == "revoked"

    @pytest.mark.asyncio
    async def test_refresh_tokens_revoked(self, db_session: AsyncSession, test_user: User):
        """Попытка обновить токены отозванным refresh-токеном вызывает 401."""
        service = AuthService(db_session)
        refresh_token = await service.create_refresh_token(test_user.id)
        # Отзываем токен
        import hashlib

        hashed = hashlib.sha256(refresh_token.encode()).hexdigest()
        result = await db_session.execute(select(Token).where(Token.token_hash == hashed))
        db_token = result.scalar_one_or_none()
        db_token.status = "revoked"
        await db_session.commit()

        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(refresh_token)
        assert exc_info.value.code == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_refresh_tokens_expired(self, db_session: AsyncSession, test_user: User):
        """Попытка обновить токены истекшим refresh-токеном вызывает 401."""
        service = AuthService(db_session)
        refresh_token = await service.create_refresh_token(test_user.id)

        # Устанавливаем дату истечения в прошлом
        import hashlib

        hashed = hashlib.sha256(refresh_token.encode()).hexdigest()
        result = await db_session.execute(select(Token).where(Token.token_hash == hashed))
        db_token = result.scalar_one_or_none()
        db_token.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
        await db_session.commit()

        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(refresh_token)
        assert exc_info.value.code == "EXPIRED_TOKEN"

    @pytest.mark.asyncio
    async def test_refresh_tokens_with_access_token(self, db_session: AsyncSession, test_user: User):
        """Попытка передать access-токен в метод refresh_tokens вызывает ошибку типа токена."""
        service = AuthService(db_session)
        access_token = service.create_access_token(test_user.id)

        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(access_token)
        assert exc_info.value.code == "INVALID_TOKEN"

    @pytest.mark.asyncio
    async def test_logout(self, db_session: AsyncSession, test_user: User):
        """Выход из системы помечает токен как revoked."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)

        service.logout(test_user.id, token_str)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        with pytest.raises(UnauthorizedException) as exc_info:
            await get_current_user(credentials=creds, db=db_session)
        assert exc_info.value.code == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_logout_nonexistent_token(self, db_session: AsyncSession, test_user: User):
        """Попытка деактивации несуществующего токена вызывает NotFoundException."""
        service = AuthService(db_session)
        with pytest.raises(NotFoundException) as exc_info:
            service.logout(test_user.id, "nonexistent-token")
        assert exc_info.value.code == "TOKEN_NOT_FOUND"

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_success(self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession):
        mock_jwt_decode.return_value = {
            "email": "googleuser@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": "Google User",
            "picture": "https://example.com/avatar.png",
        }

        service = AuthService(db_session)
        payload = GoogleSignInRequest(id_token="mock_id_token")

        tokens = await service.google_login(payload)
        assert tokens.access_token is not None
        assert tokens.refresh_token is not None
        assert tokens.user is not None
        assert tokens.user.avatar_url == "https://example.com/avatar.png"
        assert tokens.user.name == "Google User"

        # Убеждаемся, что пользователь создан
        result = await db_session.execute(select(User).where(User.email == "googleuser@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.avatar_url == "https://example.com/avatar.png"
        assert user.hashed_password is None

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_fallback_avatar_and_name_from_payload(
        self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession
    ):
        """Проверка fallback на avatar_url и name из payload при их отсутствии в Google JWT."""
        mock_jwt_decode.return_value = {
            "email": "fallbackuser@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": None,
            "picture": None,
        }

        service = AuthService(db_session)
        payload = GoogleSignInRequest(
            id_token="mock_id_token",
            avatar_url="https://example.com/fallback.png",
            name="Custom Name",
        )

        tokens = await service.google_login(payload)
        assert tokens.user is not None
        assert tokens.user.avatar_url == "https://example.com/fallback.png"
        assert tokens.user.name == "Custom Name"

        result = await db_session.execute(select(User).where(User.email == "fallbackuser@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.avatar_url == "https://example.com/fallback.png"
        assert user.name == "Custom Name"

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_updates_existing_user_avatar_and_name(
        self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession
    ):
        """Повторный вход через Google обновляет аватар и имя существующего пользователя."""
        mock_jwt_decode.return_value = {
            "email": "repeatuser@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": None,
            "picture": None,
        }

        service = AuthService(db_session)
        # 1. Первый вход без аватара
        first_payload = GoogleSignInRequest(id_token="mock_id_token_1")
        tokens_1 = await service.google_login(first_payload)
        assert tokens_1.user.avatar_url is None
        assert tokens_1.user.name == "repeatuser"

        # 2. Повторный вход с установленным аватаром и именем
        mock_jwt_decode.return_value = {
            "email": "repeatuser@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": "Updated Repeat User",
            "picture": "https://example.com/updated_avatar.png",
        }
        second_payload = GoogleSignInRequest(id_token="mock_id_token_2")
        tokens_2 = await service.google_login(second_payload)
        assert tokens_2.user.avatar_url == "https://example.com/updated_avatar.png"
        assert tokens_2.user.name == "Updated Repeat User"

        # Проверка актуального состояния в БД
        result = await db_session.execute(select(User).where(User.email == "repeatuser@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.avatar_url == "https://example.com/updated_avatar.png"
        assert user.name == "Updated Repeat User"

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_payload_avatar_overrides_stale_jwt_picture(
        self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession
    ):
        """Проверка того, что переданный клиентом avatar_url имеет приоритет над старым picture в JWT."""
        mock_jwt_decode.return_value = {
            "email": "stale_jwt_user@example.com",
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": "Old JWT Name",
            "picture": "https://example.com/old_stale_avatar.png",
        }

        service = AuthService(db_session)
        # Пользователь логинится со свежим аватаром в payload, хотя в токене старый avatar
        payload = GoogleSignInRequest(
            id_token="mock_id_token",
            avatar_url="https://example.com/new_fresh_avatar.png",
            name="New Fresh Name",
        )
        tokens = await service.google_login(payload)
        assert tokens.user is not None
        assert tokens.user.avatar_url == "https://example.com/new_fresh_avatar.png"
        assert tokens.user.name == "New Fresh Name"

        result = await db_session.execute(select(User).where(User.email == "stale_jwt_user@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.avatar_url == "https://example.com/new_fresh_avatar.png"
        assert user.name == "New Fresh Name"

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_jwks_client_reused_across_requests(self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession):
        """Клиент JWKS создаётся один раз на воркер: новый AuthService на каждый запрос не сбрасывает кэш ключей."""
        mock_jwt_decode.side_effect = [
            {"email": "jwks_google@example.com", "iss": "https://accounts.google.com", "email_verified": True},
            {"email": "jwks_google@example.com", "iss": "https://accounts.google.com", "email_verified": True},
            {"email": "jwks_apple@example.com", "iss": "https://appleid.apple.com"},
            {"email": "jwks_apple@example.com", "iss": "https://appleid.apple.com"},
        ]

        for _ in range(2):
            await AuthService(db_session).google_login(GoogleSignInRequest(id_token="mock_id_token"))
        for _ in range(2):
            await AuthService(db_session).apple_login(AppleSignInRequest(identity_token="mock_id_token"))

        created_urls = [call.args[0] for call in mock_jwk_client.call_args_list]
        assert created_urls == ["https://www.googleapis.com/oauth2/v3/certs", "https://appleid.apple.com/auth/keys"]
        assert mock_jwk_client.return_value.get_signing_key_from_jwt.call_count == 4

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_apple_login_success(self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession):
        mock_jwt_decode.return_value = {"email": "appleuser@example.com", "iss": "https://appleid.apple.com"}

        service = AuthService(db_session)
        payload = AppleSignInRequest(identity_token="mock_id_token")

        tokens = await service.apple_login(payload)
        assert tokens.access_token is not None
        assert tokens.refresh_token is not None

        # Убеждаемся, что пользователь создан
        result = await db_session.execute(select(User).where(User.email == "appleuser@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.name == "appleuser"
        assert user.avatar_url is None
        assert user.hashed_password is None

    @pytest.mark.asyncio
    async def test_delete_account_success(self, db_session: AsyncSession, test_user: User):
        """Успешное удаление аккаунта пользователя."""
        service = AuthService(db_session)
        await service.delete_account(test_user.id)

        await db_session.commit()
        result = await db_session.execute(select(User).where(User.id == test_user.id))
        deleted_user = result.scalar_one_or_none()
        assert deleted_user is None

    @pytest.mark.asyncio
    async def test_delete_account_not_found(self, db_session: AsyncSession):
        """Попытка удалить несуществующий аккаунт вызывает NotFoundException."""
        service = AuthService(db_session)
        with pytest.raises(NotFoundException) as exc_info:
            await service.delete_account(uuid.uuid4())
        assert exc_info.value.code == "USER_NOT_FOUND"


class TestGetCurrentUserDependency:
    """Тесты зависимости get_current_user."""

    @pytest.mark.asyncio
    async def test_valid_token(self, db_session: AsyncSession, test_user: User):
        """Валидный токен успешно возвращает пользователя."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        user = await get_current_user(credentials=creds, db=db_session)
        assert user.id == test_user.id
        assert user.email == test_user.email

    @pytest.mark.asyncio
    async def test_valid_token_does_not_log_debug_auth_success(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """get_current_user не генерирует избыточных логов об успешной валидации токена."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)
        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)

        with caplog.at_level(logging.DEBUG):
            user = await get_current_user(credentials=creds, db=db_session)
            assert user.id == test_user.id

        assert not any("Аутентификация успешна" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_revoked_token(self, db_session: AsyncSession, test_user: User):
        """Отозванный токен вызывает UnauthorizedException."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)
        service.logout(test_user.id, token_str)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        with pytest.raises(UnauthorizedException) as exc_info:
            await get_current_user(credentials=creds, db=db_session)
        assert exc_info.value.code == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_expired_token(self, db_session: AsyncSession, test_user: User):
        """Истекший токен вызывает UnauthorizedException."""
        import jwt

        from config_reader.config_reader import config

        expire = datetime.now(timezone.utc) - timedelta(minutes=5)
        payload = {
            "sub": str(test_user.id),
            "type": "access",
            "exp": expire,
            "jti": uuid.uuid4().hex,
        }
        token_str = jwt.encode(payload, config.secret_key, algorithm=config.algorithm)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        with pytest.raises(UnauthorizedException) as exc_info:
            await get_current_user(credentials=creds, db=db_session)
        assert exc_info.value.code == "INVALID_TOKEN"


class TestAuthEndpointsIntegration:
    """Интеграционные тесты для эндпоинтов /api/v1/auth/."""

    @pytest.mark.asyncio
    async def test_register_endpoint(self, client: AsyncClient):
        """POST /api/v1/auth/register возвращает 201 и профиль пользователя."""
        payload = {
            "email": f"api_user_{uuid.uuid4().hex[:6]}@example.com",
            "password": "Password123!",
            "name": "Api User",
        }
        response = await client.post("/api/v1/auth/register", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["status"] == "success"
        assert data["data"]["email"] == payload["email"]
        assert data["data"]["name"] == payload["name"]
        assert "hashed_password" not in data["data"]

    @pytest.mark.asyncio
    async def test_login_and_me_endpoint(self, client: AsyncClient, db_session: AsyncSession):
        """POST /api/v1/auth/login возвращает токены, GET /api/v1/auth/me возвращает профиль."""
        email = f"flow_user_{uuid.uuid4().hex[:6]}@example.com"
        password = "Password123!"
        await AuthService(db_session).register(UserRegister(email=email, password=password, name="Flow User"))

        # Login
        login_res = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert login_res.status_code == 200
        tokens = login_res.json()["data"]
        access_token = tokens["access_token"]
        refresh_token = tokens["refresh_token"]

        # GET /me with access token
        headers = {"Authorization": f"Bearer {access_token}"}
        me_res = await client.get("/api/v1/auth/me", headers=headers)
        assert me_res.status_code == 200
        assert me_res.json()["data"]["email"] == email

        # Refresh tokens
        ref_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
        assert ref_res.status_code == 200
        assert ref_res.json()["data"]["access_token"] is not None

        # Logout
        logout_res = await client.post("/api/v1/auth/logout", headers=headers)
        assert logout_res.status_code == 200
        assert logout_res.json()["status"] == "success"

        # После логаута старый access token недействителен
        me_after_logout = await client.get("/api/v1/auth/me", headers=headers)
        assert me_after_logout.status_code == 401

    @pytest.mark.asyncio
    async def test_delete_me_endpoint(self, client: AsyncClient, db_session: AsyncSession):
        """DELETE /api/v1/auth/me удаляет пользователя и возвращает 200."""
        email = f"delete_user_{uuid.uuid4().hex[:6]}@example.com"
        password = "Password123!"
        await AuthService(db_session).register(UserRegister(email=email, password=password, name="Delete User"))

        login_res = await client.post("/api/v1/auth/login", json={"email": email, "password": password})
        access_token = login_res.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {access_token}"}

        # Delete account
        del_res = await client.delete("/api/v1/auth/me", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "success"

        # После удаления старый access token недействителен
        me_after_del = await client.get("/api/v1/auth/me", headers=headers)
        assert me_after_del.status_code == 401


class TestAuthLogsWithoutPii:
    """Логи AuthService содержат user_id вместо email; при неудачном входе — только маскированный email."""

    @pytest.mark.asyncio
    async def test_register_and_login_log_user_id_not_email(
        self, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ):
        """Регистрация и успешный вход пишут в лог ID пользователя, а не email."""
        service = AuthService(db_session)
        email = f"piilog_{uuid.uuid4().hex[:6]}@example.com"
        password = "ValidPassword123!"

        with caplog.at_level(logging.INFO):
            user = await service.register(UserRegister(email=email, password=password, name="Pii User"))
            await service.login(UserLogin(email=email, password=password))

        messages = [record.getMessage() for record in caplog.records]
        assert not any(email in message for message in messages)
        assert any(str(user.id) in message and "Зарегистрирован" in message for message in messages)
        assert any(str(user.id) in message and "авторизован" in message for message in messages)

    @pytest.mark.asyncio
    async def test_duplicate_registration_logs_user_id_not_email(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Повторная регистрация логирует ID существующего пользователя без email."""
        service = AuthService(db_session)
        payload = UserRegister(email=test_user.email, password="AnotherPassword123!", name="Duplicate")

        with caplog.at_level(logging.WARNING):
            with pytest.raises(BadRequestException):
                await service.register(payload)

        messages = [record.getMessage() for record in caplog.records]
        assert not any(test_user.email in message for message in messages)
        assert any(str(test_user.id) in message for message in messages)

    @pytest.mark.asyncio
    async def test_failed_login_logs_masked_email(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Неудачный вход логирует маскированный email вида u***@example.com."""
        service = AuthService(db_session)

        with caplog.at_level(logging.WARNING):
            with pytest.raises(UnauthorizedException):
                await service.login(UserLogin(email=test_user.email, password="WrongPassword!"))

        messages = [record.getMessage() for record in caplog.records]
        assert not any(test_user.email in message for message in messages)
        assert any("u***@example.com" in message for message in messages)

    @pytest.mark.asyncio
    async def test_refresh_tokens_logs_user_id_not_email(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Ротация токенов логирует ID пользователя без email."""
        service = AuthService(db_session)
        refresh_token = await service.create_refresh_token(test_user.id)

        with caplog.at_level(logging.INFO):
            await service.refresh_tokens(refresh_token)

        messages = [record.getMessage() for record in caplog.records]
        assert not any(test_user.email in message for message in messages)
        assert any(str(test_user.id) in message and "ротация" in message for message in messages)

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_logs_registration_and_repeat_login(
        self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ):
        """Вход через Google логирует регистрацию нового пользователя и повторный вход по user_id без email."""
        email = f"googlelog_{uuid.uuid4().hex[:6]}@example.com"
        mock_jwt_decode.return_value = {
            "email": email,
            "iss": "https://accounts.google.com",
            "email_verified": True,
            "name": "Google Log",
        }
        service = AuthService(db_session)

        with caplog.at_level(logging.INFO):
            first = await service.google_login(GoogleSignInRequest(id_token="mock_id_token"))
            await service.google_login(GoogleSignInRequest(id_token="mock_id_token"))

        user_id = str(first.user.id)
        messages = [record.getMessage() for record in caplog.records]
        assert not any(email in message for message in messages)
        assert any(f"Зарегистрирован новый пользователь {user_id} через google" in message for message in messages)
        assert any(f"Пользователь {user_id} успешно авторизован через google" in message for message in messages)

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_google_login_rejected_logs_warning_without_email(
        self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession, caplog: pytest.LogCaptureFixture
    ):
        """Отклонённый вход через Google — WARNING (ошибка клиента, не сервера) без email."""
        email = f"unverified_{uuid.uuid4().hex[:6]}@example.com"
        mock_jwt_decode.return_value = {
            "email": email,
            "iss": "https://accounts.google.com",
            "email_verified": False,
        }
        service = AuthService(db_session)

        with caplog.at_level(logging.INFO):
            with pytest.raises(UnauthorizedException):
                await service.google_login(GoogleSignInRequest(id_token="mock_id_token"))

        records = [record for record in caplog.records if "Отклонён вход через google" in record.getMessage()]
        assert len(records) == 1
        assert records[0].levelno == logging.WARNING
        assert email not in records[0].getMessage()

    @pytest.mark.asyncio
    async def test_expired_access_token_is_not_logged_as_warning(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Истёкший access-токен — штатный 401 без WARNING; поддельная подпись по-прежнему WARNING."""
        expired = jwt.encode(
            {
                "sub": str(test_user.id),
                "type": "access",
                "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
                "jti": uuid.uuid4().hex,
            },
            config.secret_key,
            algorithm=config.algorithm,
        )

        with caplog.at_level(logging.DEBUG):
            with pytest.raises(UnauthorizedException):
                await get_current_user(
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials=expired), db=db_session
                )
        assert not any(record.levelno >= logging.WARNING for record in caplog.records)

        caplog.clear()
        with caplog.at_level(logging.DEBUG):
            with pytest.raises(UnauthorizedException):
                await get_current_user(
                    credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="bad.token.value"),
                    db=db_session,
                )
        assert any(record.levelno == logging.WARNING for record in caplog.records)

    @pytest.mark.asyncio
    async def test_delete_account_logs_user_id_not_email(
        self, db_session: AsyncSession, test_user: User, caplog: pytest.LogCaptureFixture
    ):
        """Удаление аккаунта логирует ID пользователя без email."""
        service = AuthService(db_session)

        with caplog.at_level(logging.INFO):
            await service.delete_account(test_user.id)

        messages = [record.getMessage() for record in caplog.records]
        assert not any(test_user.email in message for message in messages)
        assert any(str(test_user.id) in message and "удален" in message for message in messages)
