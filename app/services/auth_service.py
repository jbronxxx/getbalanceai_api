"""Сервис аутентификации, авторизации и управления пользователями."""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from typing import Union

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError as JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import (
    BadRequestException,
    ErrorCode,
    NotFoundException,
    UnauthorizedException,
)
from app.models.models import Token, User
from app.schemas.schemas import (
    AppleSignInRequest,
    GoogleSignInRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
)
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

# Схема Bearer токена для извлечения заголовка Authorization
bearer_scheme = HTTPBearer()


class AuthService:
    """Класс бизнес-логики для регистрации, входа и управления токенами пользователей."""

    bearer_scheme = HTTPBearer()

    _revoked_jtis: set[str] = set()

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def register(self, payload: UserRegister) -> User:
        """Зарегистрировать нового пользователя в системе."""
        query = select(User).where(User.email == payload.email)
        result = await self.db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            logger.warning(f"Попытка регистрации с уже зарегистрированным email: {payload.email}")
            raise BadRequestException(
                code=ErrorCode.USER_ALREADY_EXISTS,
                message="Пользователь с таким email уже зарегистрирован",
            )

        hashed_bytes = await asyncio.to_thread(bcrypt.hashpw, payload.password.encode(), bcrypt.gensalt())
        hashed = hashed_bytes.decode()
        user = User(email=payload.email, hashed_password=hashed, name=payload.name)
        self.db.add(user)
        await self.db.flush()
        await self.db.refresh(user)
        logger.info(f"Зарегистрирован новый пользователь: {user.email} (ID: {user.id})")
        return user

    async def login(self, payload: UserLogin) -> TokenResponse:
        """Аутентифицировать пользователя и выдать JWT access и refresh токены."""
        query = select(User).where(User.email == payload.email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        is_valid = False
        if user and user.hashed_password:
            is_valid = await asyncio.to_thread(bcrypt.checkpw, payload.password.encode(), user.hashed_password.encode())

        if not user or not is_valid:
            logger.warning(f"Неуспешная попытка входа для email: {payload.email}")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_CREDENTIALS,
                message="Неверный email или пароль",
            )

        access_token = self.create_access_token(user.id)
        refresh_token = await self.create_refresh_token(user.id)
        logger.info(f"Пользователь успешно авторизован: {user.email}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    async def _handle_oauth_user(self, email: str, name: str, avatar_url: str | None = None) -> TokenResponse:
        query = select(User).where(User.email == email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            user = User(email=email, name=name, avatar_url=avatar_url, hashed_password=None)
            self.db.add(user)
        else:
            if avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
        await self.db.flush()
        await self.db.refresh(user)

        access_token = self.create_access_token(user.id)
        refresh_token = await self.create_refresh_token(user.id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
        )

    async def google_login(self, payload: GoogleSignInRequest) -> TokenResponse:
        try:
            jwks_client = PyJWKClient("https://www.googleapis.com/oauth2/v3/certs")
            signing_key = jwks_client.get_signing_key_from_jwt(payload.id_token)

            # Если в конфигурации задан client_id, проверяем audience
            audience = config.google_client_id if config.google_client_id else None
            data = jwt.decode(
                payload.id_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                options={"verify_aud": bool(audience)},
            )

            email = data.get("email")
            if not email:
                raise ValueError("Email not provided by Google")

            name = data.get("name", email.split("@")[0])
            picture = data.get("picture")

            return await self._handle_oauth_user(email, name, picture)
        except Exception as e:
            logger.error(f"Google login failed: {str(e)}")
            raise UnauthorizedException(code=ErrorCode.INVALID_CREDENTIALS, message="Недействительный токен Google")

    async def apple_login(self, payload: AppleSignInRequest) -> TokenResponse:
        try:
            jwks_client = PyJWKClient("https://appleid.apple.com/auth/keys")
            signing_key = jwks_client.get_signing_key_from_jwt(payload.identity_token)

            audience = config.apple_client_id if config.apple_client_id else None
            data = jwt.decode(
                payload.identity_token,
                signing_key.key,
                algorithms=["RS256"],
                audience=audience,
                options={"verify_aud": bool(audience)},
            )

            email = data.get("email")
            if not email:
                raise ValueError("Email not provided by Apple")

            name = email.split("@")[0]  # В реальном приложении можно парсить доп. данные

            return await self._handle_oauth_user(email, name, None)
        except Exception as e:
            logger.error(f"Apple login failed: {str(e)}")
            raise UnauthorizedException(code=ErrorCode.INVALID_CREDENTIALS, message="Недействительный токен Apple")

    def logout(self, user_id: uuid.UUID, token_string: str) -> None:
        """Выйти из системы, деактивируя токен в памяти."""
        self._deactivate_token(user_id, token_string)

    async def delete_account(self, user_id: uuid.UUID) -> None:
        """Удалить аккаунт пользователя и все связанные с ним данные."""
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        if not user:
            logger.warning(f"Попытка удаления несуществующего пользователя с ID {user_id}")
            raise NotFoundException(
                code=ErrorCode.USER_NOT_FOUND,
                message="Пользователь не найден",
            )

        await self.db.delete(user)
        # Каскадное удаление данных (transactions, budgets, tokens) отработает благодаря cascade="all, delete-orphan"
        logger.info(f"Аккаунт пользователя {user.email} (ID: {user.id}) успешно удален")

    def create_access_token(self, user_id: Union[str, uuid.UUID]) -> str:
        """Сгенерировать access_token для пользователя."""
        user_id_str = str(user_id)
        expire = datetime.now(timezone.utc) + timedelta(minutes=config.access_token_expire_minutes)
        payload = {
            "sub": user_id_str,
            "type": "access",
            "exp": expire,
            "jti": uuid.uuid4().hex,
        }
        logger.debug(f"Генерация access-токена для пользователя {user_id_str} (истекает: {expire})")
        token_str = jwt.encode(payload, config.secret_key, algorithm=config.algorithm)
        return token_str

    async def create_refresh_token(self, user_id: Union[str, uuid.UUID]) -> str:
        """Сгенерировать и сохранить в БД refresh_token для пользователя."""
        user_id_str = str(user_id)
        user_uuid = uuid.UUID(user_id_str) if isinstance(user_id, str) else user_id
        expire = datetime.now(timezone.utc) + timedelta(days=config.refresh_token_expire_days)
        payload = {
            "sub": user_id_str,
            "type": "refresh",
            "exp": expire,
            "jti": uuid.uuid4().hex,
        }
        logger.debug(f"Генерация refresh-токена для пользователя {user_id_str} (истекает: {expire})")
        refresh_token_str = jwt.encode(payload, config.secret_key, algorithm=config.algorithm)

        hashed_token = hashlib.sha256(refresh_token_str.encode()).hexdigest()

        db_token = Token(
            user_id=user_uuid,
            token_hash=hashed_token,
            expires_at=expire,
            status="active",
        )
        self.db.add(db_token)
        await self.db.flush()
        await self.db.refresh(db_token)
        logger.debug(f"Refresh-токен успешно сохранен в БД для пользователя {user_id_str}")
        return refresh_token_str

    async def refresh_tokens(self, refresh_token_string: str) -> TokenResponse:
        """Обновить access_token и получить новый refresh_token по существующему refresh_token."""
        try:
            payload = jwt.decode(refresh_token_string, config.secret_key, algorithms=[config.algorithm])
            user_id = payload.get("sub")
            token_type = payload.get("type")

            if token_type and token_type != "refresh":
                logger.warning("Попытка использования токена доступа вместо refresh-токена при обновлении")
                raise UnauthorizedException(
                    code=ErrorCode.INVALID_TOKEN,
                    message="Недействительный тип токена. Ожидается refresh token",
                )

            if not user_id:
                logger.warning("Refresh-токен не содержит идентификатор пользователя (sub)")
                raise UnauthorizedException(
                    code=ErrorCode.INVALID_TOKEN,
                    message="Недействительный refresh токен",
                )
        except JWTError:
            logger.warning("Недействительная подпись или структура JWT refresh-токена")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_TOKEN,
                message="Недействительный или истекший refresh токен",
            )

        hashed_token = hashlib.sha256(refresh_token_string.encode()).hexdigest()
        query = select(Token).where(Token.token_hash == hashed_token, Token.status == "active")
        result = await self.db.execute(query)
        db_token = result.scalar_one_or_none()

        if not db_token:
            logger.warning("Refresh-токен отсутствует в базе данных либо неактивен")
            raise UnauthorizedException(
                code=ErrorCode.TOKEN_REVOKED,
                message="Refresh токен недействителен или отозван",
            )

        token_expires_at = db_token.expires_at
        if token_expires_at.tzinfo is None:
            token_expires_at = token_expires_at.replace(tzinfo=timezone.utc)

        if token_expires_at < datetime.now(timezone.utc):
            db_token.status = "expired"
            await self.db.flush()
            logger.warning(f"Истек срок действия refresh-токена в базе данных для пользователя {db_token.user_id}")
            raise UnauthorizedException(
                code=ErrorCode.EXPIRED_TOKEN,
                message="Срок действия refresh токена истек",
            )

        try:
            user_uuid = uuid.UUID(str(user_id))
        except (ValueError, TypeError):
            logger.warning(f"Некорректный формат UUID в токене: {user_id}")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_TOKEN,
                message="Недействительный refresh токен",
            )

        if db_token.user_id != user_uuid:
            logger.warning(f"Несоответствие идентификатора пользователя в токене ({user_id}) и БД ({db_token.user_id})")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_TOKEN,
                message="Недействительный refresh токен",
            )

        user_query = select(User).where(User.id == user_uuid)
        user_result = await self.db.execute(user_query)
        user = user_result.scalar_one_or_none()

        if not user:
            logger.warning(f"Пользователь с ID {db_token.user_id} не найден при ротации токенов")
            raise UnauthorizedException(
                code=ErrorCode.USER_NOT_FOUND,
                message="Пользователь не найден",
            )

        # Ротация refresh-токена: деактивируем старый refresh_token
        db_token.status = "revoked"
        await self.db.flush()

        # Генерируем новую пару токенов
        new_access_token = self.create_access_token(user.id)
        new_refresh_token = await self.create_refresh_token(user.id)

        logger.info(f"Успешная ротация токенов для пользователя: {user.email}")
        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
        )

    def _create_token(self, user_id: str) -> str:
        """Совместимый приватный метод для создания access токена."""
        return self.create_access_token(user_id)

    def _deactivate_token(self, user_id: uuid.UUID, token_string: str) -> None:
        """Деактивировать токен (при выходе пользователя)."""
        try:
            payload = jwt.decode(token_string, config.secret_key, algorithms=[config.algorithm])
            jti = payload.get("jti")
            if jti:
                AuthService._revoked_jtis.add(jti)
                logger.info(f"Токен успешно отзывен при выходе пользователя {user_id}")
                return
        except JWTError:
            pass

        logger.warning(f"Попытка отзыва несуществующего токена для пользователя {user_id}")
        raise NotFoundException(
            code=ErrorCode.TOKEN_NOT_FOUND,
            message="Токен не найден для деактивации",
        )

    async def cleanup_expired_tokens(self, retention_days: int = 30) -> int:
        """Удалить устаревшие токены."""
        from sqlalchemy import delete

        threshold = datetime.now(timezone.utc) - timedelta(days=retention_days)

        query = delete(Token).where(
            (Token.expires_at < threshold) | (Token.status.in_(["expired", "revoked"]) & (Token.created_at < threshold))
        )

        result = await self.db.execute(query)
        await self.db.flush()
        deleted_count = result.rowcount
        logger.info(f"Очистка токенов: удалено {deleted_count} устаревших токенов (порог: {threshold.isoformat()})")
        return deleted_count


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """Зависимость FastAPI (Dependency) для получения текущего авторизованного пользователя."""
    token = credentials.credentials

    try:
        payload = jwt.decode(token, config.secret_key, algorithms=[config.algorithm])
        user_id = payload.get("sub")
        token_type = payload.get("type")
        jti = payload.get("jti")

        if token_type and token_type != "access":
            logger.warning("Попытка аутентификации с использованием не-access токена")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_TOKEN,
                message="Недействительный тип токена. Ожидается access token",
            )
    except JWTError:
        logger.warning("Ошибка валидации подписи JWT access-токена")
        raise UnauthorizedException(
            code=ErrorCode.INVALID_TOKEN,
            message="Недействительный или истекший токен авторизации",
        )

    if not user_id:
        logger.warning("JWT access-токен не содержит идентификатор пользователя (sub)")
        raise UnauthorizedException(
            code=ErrorCode.INVALID_TOKEN,
            message="Недействительный токен авторизации",
        )

    try:
        user_uuid = uuid.UUID(str(user_id))
    except (ValueError, TypeError):
        logger.warning(f"Некорректный UUID в токене авторизации: {user_id}")
        raise UnauthorizedException(
            code=ErrorCode.INVALID_TOKEN,
            message="Недействительный токен авторизации",
        )

    if jti in AuthService._revoked_jtis:
        raise UnauthorizedException(
            code=ErrorCode.TOKEN_REVOKED,
            message="Токен отозван или недействителен",
        )

    query = select(User).where(User.id == user_uuid)
    result = await db.execute(query)
    user = result.scalar_one_or_none()

    if not user:
        logger.warning(f"Пользователь с ID {user_id} из токена не найден в базы данных")
        raise UnauthorizedException(
            code=ErrorCode.USER_NOT_FOUND,
            message="Пользователь не найден",
        )

    logger.debug(f"Аутентификация успешна для пользователя: {user.email}")
    return user
