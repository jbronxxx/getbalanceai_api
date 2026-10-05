"""Юнит и интеграционные тесты для AuthService и эндпоинтов авторизации."""

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

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
            "name": "Google User",
            "picture": "https://example.com/avatar.png",
        }

        service = AuthService(db_session)
        payload = GoogleSignInRequest(id_token="mock_id_token")

        tokens = await service.google_login(payload)
        assert tokens.access_token is not None
        assert tokens.refresh_token is not None

        # Убеждаемся, что пользователь создан
        result = await db_session.execute(select(User).where(User.email == "googleuser@example.com"))
        user = result.scalar_one_or_none()
        assert user is not None
        assert user.avatar_url == "https://example.com/avatar.png"
        assert user.hashed_password is None

    @pytest.mark.asyncio
    @patch("app.services.auth_service.PyJWKClient")
    @patch("app.services.auth_service.jwt.decode")
    async def test_apple_login_success(self, mock_jwt_decode, mock_jwk_client, db_session: AsyncSession):
        mock_jwt_decode.return_value = {"email": "appleuser@example.com"}

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
