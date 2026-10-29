"""Юнит и интеграционные тесты для AuthService и эндпоинтов авторизации."""

import hashlib
import logging
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import fakeredis
import jwt
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from httpx import AsyncClient
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import (
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from app.models.models import Token, User
from app.redis import get_revoked_jti_key
from app.schemas.schemas import AppleSignInRequest, GoogleSignInRequest, TokenResponse
from app.services.auth_service import AuthService, get_current_user
from config_reader.config_reader import config


async def _create_user(db_session: AsyncSession, email: str) -> User:
    """Создать пользователя напрямую: аккаунты появляются только через вход Google/Apple."""
    user = User(email=email, name="Session User")
    db_session.add(user)
    await db_session.flush()
    return user


class TestAuthServiceUnit:
    """Юнит-тесты бизнес-логики AuthService."""

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

        await service.logout(test_user.id, token_str)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        with pytest.raises(UnauthorizedException) as exc_info:
            await get_current_user(credentials=creds, db=db_session)
        assert exc_info.value.code == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_logout_nonexistent_token(self, db_session: AsyncSession, test_user: User):
        """Попытка деактивации несуществующего токена вызывает NotFoundException."""
        service = AuthService(db_session)
        with pytest.raises(NotFoundException) as exc_info:
            await service.logout(test_user.id, "nonexistent-token")
        assert exc_info.value.code == "TOKEN_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_logout_token_without_exp(self, db_session: AsyncSession, test_user: User):
        """Токен без срока действия не отзывается: ключ в Redis был бы вечным."""
        token_str = jwt.encode(
            {"sub": str(test_user.id), "type": "access", "jti": uuid.uuid4().hex},
            config.secret_key,
            algorithm=config.algorithm,
        )
        with pytest.raises(NotFoundException) as exc_info:
            await AuthService(db_session).logout(test_user.id, token_str)
        assert exc_info.value.code == "TOKEN_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_logout_stores_jti_in_redis_with_token_ttl(
        self, db_session: AsyncSession, test_user: User, mock_redis_client: fakeredis.FakeAsyncRedis
    ):
        """Logout записывает jti в Redis с TTL не больше оставшегося срока жизни токена."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)
        jti = jwt.decode(token_str, config.secret_key, algorithms=[config.algorithm])["jti"]

        await service.logout(test_user.id, token_str)

        ttl = await mock_redis_client.ttl(get_revoked_jti_key(jti))
        assert 0 < ttl <= config.access_token_expire_minutes * 60

    @pytest.mark.asyncio
    async def test_logout_visible_to_another_worker(
        self, db_session: AsyncSession, test_user: User, mock_redis_client: fakeredis.FakeAsyncRedis
    ):
        """Отзыв, записанный в Redis другим воркером, отклоняет токен без локального состояния процесса."""
        token_str = AuthService(db_session).create_access_token(test_user.id)
        jti = jwt.decode(token_str, config.secret_key, algorithms=[config.algorithm])["jti"]
        await mock_redis_client.set(get_revoked_jti_key(jti), "1", ex=60)

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        with pytest.raises(UnauthorizedException) as exc_info:
            await get_current_user(credentials=creds, db=db_session)
        assert exc_info.value.code == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_logout_redis_failure(
        self, db_session: AsyncSession, test_user: User, mock_redis_client: fakeredis.FakeAsyncRedis
    ):
        """При сбое Redis logout возвращает 503, а токен остаётся действующим — без ложного успеха."""
        service = AuthService(db_session)
        token_str = service.create_access_token(test_user.id)

        with patch.object(mock_redis_client, "set", AsyncMock(side_effect=RedisConnectionError("Connection refused"))):
            with pytest.raises(ServiceUnavailableException) as exc_info:
                await service.logout(test_user.id, token_str)
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"
        assert exc_info.value.status_code == 503

        creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token_str)
        user = await get_current_user(credentials=creds, db=db_session)
        assert user.id == test_user.id

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
        await service.logout(test_user.id, token_str)

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
    async def test_session_me_refresh_logout_flow(self, client: AsyncClient, db_session: AsyncSession):
        """Сессия: GET /me возвращает профиль, /refresh ротирует пару, после /logout access-токен недействителен."""
        email = f"flow_user_{uuid.uuid4().hex[:6]}@example.com"
        tokens = await self._login_session(db_session, email)
        access_token = tokens.access_token
        refresh_token = tokens.refresh_token

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
        tokens = await self._login_session(db_session, f"delete_user_{uuid.uuid4().hex[:6]}@example.com")
        access_token = tokens.access_token
        headers = {"Authorization": f"Bearer {access_token}"}

        # Delete account
        del_res = await client.delete("/api/v1/auth/me", headers=headers)
        assert del_res.status_code == 200
        assert del_res.json()["status"] == "success"

        # После удаления старый access token недействителен
        me_after_del = await client.get("/api/v1/auth/me", headers=headers)
        assert me_after_del.status_code == 401

    @staticmethod
    async def _login_session(db_session: AsyncSession, email: str, register: bool = True) -> TokenResponse:
        """Выдать пару токенов через сервис и зафиксировать её: rollback после ошибки запроса её не сотрёт."""
        if register:
            user = await _create_user(db_session, email)
        else:
            user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        access_token, refresh_token = await AuthService(db_session)._issue_token_pair(user.id)
        await db_session.commit()
        return TokenResponse(access_token=access_token, refresh_token=refresh_token)

    @pytest.mark.asyncio
    async def test_login_links_access_token_to_refresh_session(self, db_session: AsyncSession):
        """Access-токен содержит sid — id строки своего refresh-токена в таблице tokens."""
        tokens = await self._login_session(db_session, f"sid_{uuid.uuid4().hex[:6]}@example.com")

        sid = jwt.decode(tokens.access_token, config.secret_key, algorithms=[config.algorithm])["sid"]
        db_token = await db_session.get(Token, uuid.UUID(sid))
        assert db_token is not None
        assert db_token.status == "active"
        assert db_token.token_hash == hashlib.sha256(tokens.refresh_token.encode()).hexdigest()

    @pytest.mark.asyncio
    async def test_logout_revokes_refresh_token_of_session(self, client: AsyncClient, db_session: AsyncSession):
        """После logout refresh-токен той же сессии не обменивается на новую пару (401 TOKEN_REVOKED)."""
        tokens = await self._login_session(db_session, f"logout_rt_{uuid.uuid4().hex[:6]}@example.com")

        logout_res = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {tokens.access_token}"})
        assert logout_res.status_code == 200

        refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
        assert refresh_res.status_code == 401
        assert refresh_res.json()["code"] == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_logout_keeps_other_sessions(self, client: AsyncClient, db_session: AsyncSession):
        """Logout на одном устройстве не отзывает refresh-токен другой сессии пользователя."""
        email = f"two_devices_{uuid.uuid4().hex[:6]}@example.com"
        phone = await self._login_session(db_session, email)
        tablet = await self._login_session(db_session, email, register=False)

        logout_res = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {phone.access_token}"})
        assert logout_res.status_code == 200

        refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": tablet.refresh_token})
        assert refresh_res.status_code == 200

    @pytest.mark.asyncio
    async def test_logout_after_refresh_revokes_rotated_session(self, client: AsyncClient, db_session: AsyncSession):
        """Access-токен после ротации связан с новым refresh-токеном, и logout отзывает именно его."""
        tokens = await self._login_session(db_session, f"rotated_{uuid.uuid4().hex[:6]}@example.com")
        rotated_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
        rotated = rotated_res.json()["data"]

        logout_res = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {rotated['access_token']}"})
        assert logout_res.status_code == 200

        refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": rotated["refresh_token"]})
        assert refresh_res.status_code == 401
        assert refresh_res.json()["code"] == "TOKEN_REVOKED"

    @pytest.mark.asyncio
    async def test_logout_endpoint_redis_unavailable(
        self, client: AsyncClient, db_session: AsyncSession, mock_redis_client: fakeredis.FakeAsyncRedis
    ):
        """POST /api/v1/auth/logout при недоступном Redis возвращает 503, отзыв refresh-токена откатывается."""
        tokens = await self._login_session(db_session, f"logout_redis_{uuid.uuid4().hex[:6]}@example.com")

        with patch.object(mock_redis_client, "set", AsyncMock(side_effect=RedisConnectionError("Connection refused"))):
            response = await client.post("/api/v1/auth/logout", headers={"Authorization": f"Bearer {tokens.access_token}"})

        assert response.status_code == 503
        body = response.json()
        assert body["status"] == "error"
        assert body["code"] == "SERVICE_UNAVAILABLE"

        refresh_res = await client.post("/api/v1/auth/refresh", json={"refresh_token": tokens.refresh_token})
        assert refresh_res.status_code == 200

    @pytest.mark.asyncio
    async def test_delete_me_endpoint_redis_unavailable(
        self, client: AsyncClient, db_session: AsyncSession, mock_redis_client: fakeredis.FakeAsyncRedis
    ):
        """DELETE /api/v1/auth/me при недоступном Redis возвращает 503 и не удаляет аккаунт."""
        email = f"delete_redis_{uuid.uuid4().hex[:6]}@example.com"
        user = await _create_user(db_session, email)
        # id читаем до commit: после него атрибуты объекта истекают и ленивая подгрузка падает вне greenlet
        user_id = user.id
        # Фиксируем пользователя: при ошибке запроса override get_db откатывает сессию
        await db_session.commit()
        headers = {"Authorization": f"Bearer {AuthService(db_session).create_access_token(user_id)}"}

        with patch.object(mock_redis_client, "set", AsyncMock(side_effect=RedisConnectionError("Connection refused"))):
            response = await client.delete("/api/v1/auth/me", headers=headers)

        assert response.status_code == 503
        assert response.json()["code"] == "SERVICE_UNAVAILABLE"
        result = await db_session.execute(select(User).where(User.id == user_id))
        assert result.scalar_one_or_none() is not None


class TestAuthLogsWithoutPii:
    """Логи AuthService содержат user_id вместо email."""

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


class TestRefreshRotationAtomicity:
    """Ротация refresh-токена атомарна; повтор давно отозванного токена отзывает все сессии владельца."""

    @staticmethod
    async def _active_sessions(db_session: AsyncSession, user_id: uuid.UUID) -> int:
        db_session.expire_all()
        result = await db_session.execute(select(Token).where(Token.user_id == user_id, Token.status == "active"))
        return len(result.scalars().all())

    @staticmethod
    async def _token_row(db_session: AsyncSession, refresh_token: str) -> Token:
        hashed = hashlib.sha256(refresh_token.encode()).hexdigest()
        return (await db_session.execute(select(Token).where(Token.token_hash == hashed))).scalar_one()

    @pytest.mark.asyncio
    async def test_rotation_records_revoked_at(self, db_session: AsyncSession, test_user: User):
        """Ротированный токен помечается revoked с временем отзыва."""
        service = AuthService(db_session)
        _, refresh_token = await service._issue_token_pair(test_user.id)

        await service.refresh_tokens(refresh_token)

        row = await self._token_row(db_session, refresh_token)
        assert row.status == "revoked"
        assert row.revoked_at is not None

    @pytest.mark.asyncio
    async def test_second_use_of_same_token_gets_no_new_session(self, db_session: AsyncSession, test_user: User):
        """Из двух запросов с одним токеном новую пару получает только первый (проигравший гонку — 401)."""
        service = AuthService(db_session)
        _, refresh_token = await service._issue_token_pair(test_user.id)

        await service.refresh_tokens(refresh_token)
        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(refresh_token)

        assert exc_info.value.code == "TOKEN_REVOKED"
        assert await self._active_sessions(db_session, test_user.id) == 1

    @pytest.mark.asyncio
    async def test_retry_within_grace_keeps_other_sessions(self, db_session: AsyncSession, test_user: User):
        """Повтор сразу после ротации (ответ не дошёл) не отзывает сессии на других устройствах."""
        service = AuthService(db_session)
        _, other_device_refresh = await service._issue_token_pair(test_user.id)
        _, refresh_token = await service._issue_token_pair(test_user.id)
        await service.refresh_tokens(refresh_token)

        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(refresh_token)

        assert (await self._token_row(db_session, other_device_refresh)).status == "active"
        assert await self._active_sessions(db_session, test_user.id) == 2

    @pytest.mark.asyncio
    async def test_reuse_after_grace_revokes_all_sessions(self, db_session: AsyncSession, test_user: User):
        """Давно отозванный токен считается украденным: отзываются все активные сессии, и отзыв фиксируется."""
        # id читаем до commit: после него атрибуты истекают и ленивая подгрузка падает вне greenlet
        user_id = test_user.id
        service = AuthService(db_session)
        _, other_device_refresh = await service._issue_token_pair(user_id)
        _, stolen_refresh = await service._issue_token_pair(user_id)
        await service.refresh_tokens(stolen_refresh)
        row = await self._token_row(db_session, stolen_refresh)
        row.revoked_at = datetime.now(timezone.utc) - timedelta(hours=1)
        await db_session.commit()

        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(stolen_refresh)
        # Отзыв переживает откат транзакции запроса, который выполняет get_db после 401
        await db_session.rollback()

        assert exc_info.value.code == "TOKEN_REVOKED"
        assert await self._active_sessions(db_session, user_id) == 0
        assert (await self._token_row(db_session, other_device_refresh)).status == "revoked"

    @pytest.mark.asyncio
    async def test_logged_out_token_reuse_revokes_all_sessions(self, db_session: AsyncSession, test_user: User):
        """Refresh-токен вышедшей сессии без времени отзыва (до миграции) тоже считается украденным."""
        # id читаем до commit: после него атрибуты истекают и ленивая подгрузка падает вне greenlet
        user_id = test_user.id
        service = AuthService(db_session)
        await service._issue_token_pair(user_id)
        _, old_refresh = await service._issue_token_pair(user_id)
        row = await self._token_row(db_session, old_refresh)
        row.status = "revoked"
        row.revoked_at = None
        await db_session.commit()

        with pytest.raises(UnauthorizedException):
            await service.refresh_tokens(old_refresh)

        assert await self._active_sessions(db_session, user_id) == 0

    @pytest.mark.asyncio
    async def test_refresh_token_of_other_user_rejected(self, db_session: AsyncSession, test_user: User):
        """Токен с sub чужого пользователя не ротирует строку владельца."""
        # id читаем до commit: после него атрибуты истекают и ленивая подгрузка падает вне greenlet
        user_id = test_user.id
        service = AuthService(db_session)
        _, refresh_token = await service._issue_token_pair(user_id)
        payload = jwt.decode(refresh_token, config.secret_key, algorithms=[config.algorithm])
        payload["sub"] = str(uuid.uuid4())
        forged = jwt.encode(payload, config.secret_key, algorithm=config.algorithm)
        row = await self._token_row(db_session, refresh_token)
        row.token_hash = hashlib.sha256(forged.encode()).hexdigest()
        await db_session.commit()

        with pytest.raises(UnauthorizedException) as exc_info:
            await service.refresh_tokens(forged)

        assert exc_info.value.code == "INVALID_TOKEN"
        assert await self._active_sessions(db_session, user_id) == 1

    @pytest.mark.asyncio
    async def test_logout_records_revoked_at(self, db_session: AsyncSession, test_user: User):
        """Выход помечает refresh-токен сессии revoked с временем отзыва."""
        service = AuthService(db_session)
        access_token, refresh_token = await service._issue_token_pair(test_user.id)

        await service.logout(test_user.id, access_token)

        row = await self._token_row(db_session, refresh_token)
        assert row.status == "revoked"
        assert row.revoked_at is not None

    @pytest.mark.asyncio
    async def test_refresh_endpoint_reuse_blocks_rotated_session(self, client: AsyncClient, db_session: AsyncSession):
        """Через API: после повторного предъявления украденного токена новая пара злоумышленника тоже не работает."""
        user = await _create_user(db_session, f"reuse_{uuid.uuid4().hex[:6]}@example.com")
        _, stolen_refresh = await AuthService(db_session)._issue_token_pair(user.id)
        await db_session.commit()

        rotated = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen_refresh})
        assert rotated.status_code == 200
        new_refresh = rotated.json()["data"]["refresh_token"]
        row = await self._token_row(db_session, stolen_refresh)
        row.revoked_at = datetime.now(timezone.utc) - timedelta(minutes=5)
        await db_session.commit()

        reuse = await client.post("/api/v1/auth/refresh", json={"refresh_token": stolen_refresh})
        after = await client.post("/api/v1/auth/refresh", json={"refresh_token": new_refresh})

        assert reuse.status_code == 401
        assert reuse.json()["code"] == "TOKEN_REVOKED"
        assert after.status_code == 401
