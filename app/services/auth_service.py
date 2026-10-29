"""Сервис аутентификации, авторизации и управления пользователями."""

import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Union

import bcrypt
import jwt
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient
from jwt.exceptions import InvalidTokenError as JWTError
from redis.exceptions import RedisError
from sqlalchemy import Numeric, case, func, literal, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import (
    BadRequestException,
    ErrorCode,
    NotFoundException,
    ServiceUnavailableException,
    UnauthorizedException,
)
from app.models.models import SUPPORTED_CURRENCIES, Token, Transaction, User
from app.redis import get_redis, get_revoked_jti_key
from app.schemas.schemas import (
    AppleSignInRequest,
    GoogleSignInRequest,
    TokenResponse,
    UserLogin,
    UserRegister,
    UserResponse,
    UserUpdateRequest,
)
from app.services.budget_service import BudgetService
from app.services.currency_service import CurrencyService
from config_reader.config_reader import config
from logger.logger import get_logger, mask_email

logger = get_logger(__name__)

# Схема Bearer токена для извлечения заголовка Authorization
bearer_scheme = HTTPBearer()

# Допустимые значения claim iss в ID-токенах провайдеров
GOOGLE_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
APPLE_ISSUERS = ("https://appleid.apple.com",)


class AuthService:
    """Класс бизнес-логики для регистрации, входа и управления токенами пользователей."""

    bearer_scheme = HTTPBearer()

    # Клиенты JWKS живут на уровне класса: сервис создаётся на каждый запрос, и в атрибуте экземпляра
    # кэш ключей провайдера терялся бы, заставляя скачивать их при каждом входе
    _google_jwks_client: PyJWKClient | None = None
    _apple_jwks_client: PyJWKClient | None = None

    def __init__(self, db: AsyncSession):
        """Инициализация сервиса с сессией базы данных."""
        self.db = db

    async def register(self, payload: UserRegister) -> User:
        """Зарегистрировать нового пользователя в системе."""
        query = select(User).where(User.email == payload.email)
        result = await self.db.execute(query)
        existing = result.scalar_one_or_none()

        if existing:
            logger.warning(f"Попытка повторной регистрации существующего пользователя {existing.id}")
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
        logger.info(f"Зарегистрирован новый пользователь {user.id}")
        return user

    async def update_profile(self, user_id: uuid.UUID, payload: UserUpdateRequest) -> User:
        """Обновить профиль пользователя (имя, аватар, базовая валюта).

        Аргументы:
            user_id (uuid.UUID): Идентификатор пользователя.
            payload (UserUpdateRequest): Новые данные профиля.

        Возвращает:
            User: Обновленный объект пользователя.
        """
        query = select(User).where(User.id == user_id)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        if not user:
            logger.warning(f"Попытка обновления несуществующего пользователя: {user_id}")
            raise NotFoundException(
                code=ErrorCode.USER_NOT_FOUND,
                message="Пользователь не найден",
            )

        if payload.name is not None:
            user.name = payload.name.strip()
        if payload.avatar_url is not None:
            user.avatar_url = payload.avatar_url.strip() if payload.avatar_url else None
        if payload.base_currency is not None:
            new_currency = payload.base_currency.strip().upper()
            old_currency = user.base_currency or "USD"
            if new_currency != old_currency:
                user.base_currency = new_currency
                await self.db.flush()
                await self._recalculate_user_finances(
                    user_id=user.id,
                    old_currency=old_currency,
                    new_currency=new_currency,
                )

        await self.db.flush()
        await self.db.refresh(user)
        logger.info(f"Профиль пользователя {user_id} успешно обновлен")
        return user

    async def _recalculate_user_finances(
        self,
        user_id: uuid.UUID,
        old_currency: str,
        new_currency: str,
    ) -> None:
        """Пересчитать базовые суммы транзакций при смене базовой валюты пользователя.

        Выполняется единым пакетным SQL UPDATE без загрузки тысяч ORM-объектов в память.
        Для транзакций в новой базовой валюте exchange_rate устанавливается в 1.0, а base_amount = amount.
        Лимиты бюджетов не пересчитываются: у каждого своя валюта (budgets.currency). Траты по
        лимитам зависят от base_amount, поэтому ETag бюджетов инвалидируется.
        """
        logger.info(
            f"Запущен пересчет финансов пользователя {user_id} в связи со сменой базовой валюты: "
            f"{old_currency} -> {new_currency}"
        )
        currency_service = CurrencyService()
        rates_response = await currency_service.get_rates()
        rates = rates_response.rates

        # 1. Пакетный пересчет транзакций одним SQL UPDATE (без N+1 и загрузки объектов в память)
        rate_cases = {
            c: (Decimal("1.0") if c == new_currency else CurrencyService.calculate_cross_rate(c, new_currency, rates))
            for c in SUPPORTED_CURRENCIES
        }

        # Курс передаётся как NUMERIC без масштаба: иначе литерал наследует тип amount (Numeric(18, 2)),
        # asyncpg приводит параметр к NUMERIC(18, 2), и курс 1/12800 (UZS -> USD) округлялся до 0.00
        rate_whens = [(Transaction.currency == curr, literal(rate, Numeric())) for curr, rate in rate_cases.items()]
        exchange_rate_expr = case(*rate_whens, else_=literal(Decimal("1.0"), Numeric()))

        base_whens = [
            (
                Transaction.currency == curr,
                (
                    Transaction.amount
                    if curr == new_currency
                    else func.round(Transaction.amount * literal(rate, Numeric()), 2)
                ),
            )
            for curr, rate in rate_cases.items()
        ]
        base_amount_expr = case(*base_whens, else_=Transaction.amount)

        tx_stmt = (
            update(Transaction)
            .where(Transaction.user_id == user_id)
            .values(
                exchange_rate=exchange_rate_expr,
                base_amount=base_amount_expr,
            )
        )
        await self.db.execute(tx_stmt)

        logger.info(f"Успешно пересчитаны транзакции в БД для пользователя {user_id}")

        # 2. Инвалидация ETag кэша бюджетов пользователя
        await BudgetService.invalidate_etag(user_id)

        # 3. Инвалидация кэша инсайтов AI в Redis без блокировки ключей
        try:
            from app.services.ai_service import AIService

            await AIService.invalidate_cache(user_id)
            logger.info(f"Инвалидирован кэш инсайтов для пользователя {user_id}")
        except Exception as err:
            logger.warning(f"Не удалось инвалидировать кэш инсайтов в Redis при смене валюты: {err}")

    async def login(self, payload: UserLogin) -> TokenResponse:
        """Аутентифицировать пользователя и выдать JWT access и refresh токены."""
        query = select(User).where(User.email == payload.email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()

        is_valid = False
        if user and user.hashed_password:
            is_valid = await asyncio.to_thread(bcrypt.checkpw, payload.password.encode(), user.hashed_password.encode())

        if not user or not is_valid:
            logger.warning(f"Неуспешная попытка входа для email: {mask_email(payload.email)}")
            raise UnauthorizedException(
                code=ErrorCode.INVALID_CREDENTIALS,
                message="Неверный email или пароль",
            )

        access_token, refresh_token = await self._issue_token_pair(user.id)
        logger.info(f"Пользователь {user.id} успешно авторизован по email")
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        )

    async def _handle_oauth_user(self, provider: str, email: str, name: str, avatar_url: str | None = None) -> TokenResponse:
        """Найти или создать пользователя по email из ID-токена провайдера и выдать пару токенов."""
        query = select(User).where(User.email == email)
        result = await self.db.execute(query)
        user = result.scalar_one_or_none()
        is_new = user is None

        if not user:
            user = User(email=email, name=name, avatar_url=avatar_url, hashed_password=None)
            self.db.add(user)
        else:
            if avatar_url and user.avatar_url != avatar_url:
                user.avatar_url = avatar_url
            if name and user.name == user.email.split("@")[0] and name != user.name:
                user.name = name
        await self.db.flush()
        await self.db.refresh(user)

        access_token, refresh_token = await self._issue_token_pair(user.id)
        if is_new:
            logger.info(f"Зарегистрирован новый пользователь {user.id} через {provider}")
        else:
            logger.info(f"Пользователь {user.id} успешно авторизован через {provider}")
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",
            user=UserResponse.model_validate(user),
        )

    async def google_login(self, payload: GoogleSignInRequest) -> TokenResponse:
        try:
            # Получаем кэшированный клиент JWKS для Google
            if AuthService._google_jwks_client is None:
                AuthService._google_jwks_client = PyJWKClient(
                    "https://www.googleapis.com/oauth2/v3/certs",
                    cache_jwk_set=True,
                    lifespan=3600,
                )
            data = await self._decode_oauth_token(
                payload.id_token, AuthService._google_jwks_client, config.google_client_ids, GOOGLE_ISSUERS
            )

            email = data.get("email")
            if not email:
                raise ValueError("Email not provided by Google")
            # Google отдаёт email_verified булевым значением, в старых токенах — строкой
            if data.get("email_verified") not in (True, "true"):
                raise ValueError("Google email is not verified")

            name = payload.name or data.get("name") or email.split("@")[0]
            picture = payload.avatar_url or data.get("picture")

            return await self._handle_oauth_user("google", email, name, picture)
        except Exception as e:
            logger.warning(f"Отклонён вход через google: {e}")
            raise UnauthorizedException(code=ErrorCode.INVALID_CREDENTIALS, message="Недействительный токен Google")

    async def apple_login(self, payload: AppleSignInRequest) -> TokenResponse:
        try:
            # Получаем кэшированный клиент JWKS для Apple
            if AuthService._apple_jwks_client is None:
                AuthService._apple_jwks_client = PyJWKClient(
                    "https://appleid.apple.com/auth/keys",
                    cache_jwk_set=True,
                    lifespan=3600,
                )
            data = await self._decode_oauth_token(
                payload.identity_token, AuthService._apple_jwks_client, config.apple_client_ids, APPLE_ISSUERS
            )

            email = data.get("email")
            if not email:
                raise ValueError("Email not provided by Apple")

            name = email.split("@")[0]  # В реальном приложении можно парсить доп. данные

            return await self._handle_oauth_user("apple", email, name, None)
        except Exception as e:
            logger.warning(f"Отклонён вход через apple: {e}")
            raise UnauthorizedException(code=ErrorCode.INVALID_CREDENTIALS, message="Недействительный токен Apple")

    @staticmethod
    async def _decode_oauth_token(
        token: str, jwks_client: PyJWKClient, audiences: list[str], issuers: tuple[str, ...]
    ) -> dict:
        """Проверить подпись, срок, audience и issuer ID-токена внешнего провайдера.

        Токен принимается, только если выпущен ожидаемым провайдером (iss) для одного из
        наших приложений (aud). Без настроенных client ID вход отклоняется: иначе подошёл бы
        токен, выпущенный провайдером для любого стороннего приложения.
        """
        if not audiences:
            raise ValueError("OAuth client ID is not configured")

        signing_key = await asyncio.to_thread(jwks_client.get_signing_key_from_jwt, token)
        data = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audiences,
            options={"require": ["exp", "iat", "iss", "aud"]},
        )
        # PyJWT 2.8 сверяет iss только с одной строкой, а у Google два допустимых значения
        if data.get("iss") not in issuers:
            raise ValueError("Invalid token issuer")
        return data

    async def logout(self, user_id: uuid.UUID, token_string: str) -> None:
        """Завершить сессию: отозвать refresh-токен в БД и access-токен в Redis.

        Refresh-токен сессии находится по claim sid access-токена и помечается revoked, иначе
        по нему после выхода можно получить новую пару. jti access-токена записывается в Redis
        на оставшийся срок его жизни — это хранилище отзывов, общее для всех воркеров.
        Выход считается завершённым только после подтверждённой записи: при сбое Redis
        поднимается ServiceUnavailableException, транзакция откатывается вместе с отзывом
        refresh-токена, и клиент не считает живой токен отозванным.
        """
        try:
            payload = jwt.decode(
                token_string, config.secret_key, algorithms=[config.algorithm], options={"require": ["exp", "jti"]}
            )
        except JWTError:
            payload = {}

        jti = payload.get("jti")
        if not jti:
            logger.warning(f"Попытка отзыва несуществующего токена для пользователя {user_id}")
            raise NotFoundException(
                code=ErrorCode.TOKEN_NOT_FOUND,
                message="Токен не найден для деактивации",
            )

        session_id = payload.get("sid")
        if session_id:
            await self.db.execute(
                update(Token)
                .where(Token.id == uuid.UUID(session_id), Token.user_id == user_id, Token.status == "active")
                .values(status="revoked")
            )

        # Ключ живёт не дольше самого токена: после exp токен отклоняется по сроку
        expires_in = int(payload["exp"] - datetime.now(timezone.utc).timestamp())
        try:
            await get_redis().set(get_revoked_jti_key(jti), "1", ex=max(expires_in, 1))
        except RedisError as err:
            logger.error(f"Не удалось отозвать токен пользователя {user_id}: Redis недоступен ({type(err).__name__})")
            raise ServiceUnavailableException(message="Не удалось завершить выход, повторите попытку позже")

        logger.info(f"Токен успешно отозван при выходе пользователя {user_id}")

    async def find_users_by_email(self, email: str) -> list[User]:
        """Найти пользователей по email без учёта регистра (для административных операций)."""
        query = select(User).where(func.lower(User.email) == email.strip().lower())
        result = await self.db.execute(query)
        return list(result.scalars().all())

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
        logger.info(f"Аккаунт пользователя {user.id} успешно удален")

        # Очистка связанных ключей Redis
        from app.redis import (
            get_budgets_version_key,
            get_insights_user_patterns,
            get_redis,
        )

        redis_client = get_redis()
        # Удаляем версию бюджета
        await redis_client.delete(get_budgets_version_key(user.id))
        # Инвалидация всех инсайт‑ключей пользователя
        for pattern in get_insights_user_patterns(user.id):
            async for key in redis_client.scan_iter(match=pattern):
                await redis_client.unlink(key)

    async def _issue_token_pair(self, user_id: uuid.UUID) -> tuple[str, str]:
        """Выдать связанную пару access и refresh токенов одной сессии.

        id строки refresh-токена в БД попадает в access-токен как claim sid: по нему logout
        отзывает refresh-токен именно этого устройства, не затрагивая другие сессии пользователя.
        """
        session_id = uuid.uuid4()
        refresh_token = await self.create_refresh_token(user_id, session_id=session_id)
        access_token = self.create_access_token(user_id, session_id=session_id)
        return access_token, refresh_token

    def create_access_token(self, user_id: Union[str, uuid.UUID], session_id: uuid.UUID | None = None) -> str:
        """Сгенерировать access_token для пользователя (с sid, если токен выдан в паре с refresh)."""
        user_id_str = str(user_id)
        expire = datetime.now(timezone.utc) + timedelta(minutes=config.access_token_expire_minutes)
        payload = {
            "sub": user_id_str,
            "type": "access",
            "exp": expire,
            "jti": uuid.uuid4().hex,
        }
        if session_id is not None:
            payload["sid"] = str(session_id)
        logger.debug(f"Генерация access-токена для пользователя {user_id_str} (истекает: {expire})")
        token_str = jwt.encode(payload, config.secret_key, algorithm=config.algorithm)
        return token_str

    async def create_refresh_token(self, user_id: Union[str, uuid.UUID], session_id: uuid.UUID | None = None) -> str:
        """Сгенерировать и сохранить в БД refresh_token для пользователя; session_id — id строки в tokens."""
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
            id=session_id or uuid.uuid4(),
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
        new_access_token, new_refresh_token = await self._issue_token_pair(user.id)

        logger.info(f"Успешная ротация токенов для пользователя {user.id}")
        return TokenResponse(
            access_token=new_access_token,
            refresh_token=new_refresh_token,
            token_type="bearer",
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
    except jwt.ExpiredSignatureError:
        # Штатная ситуация: клиент получит 401 и обновит пару по refresh-токену
        logger.debug("Истёк срок действия access-токена")
        raise UnauthorizedException(
            code=ErrorCode.INVALID_TOKEN,
            message="Недействительный или истекший токен авторизации",
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

    if await get_redis().exists(get_revoked_jti_key(jti)):
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

    return user
