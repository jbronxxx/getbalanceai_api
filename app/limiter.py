"""Модуль настройки ограничения частоты запросов (Rate Limiting).

Предоставляет синглтон Limiter для защиты эндпоинтов от спама и brute-force атак.
"""

import jwt
from fastapi import Request
from jwt.exceptions import InvalidTokenError as JWTError
from slowapi import Limiter
from slowapi.util import get_remote_address

from config_reader.config_reader import config


def get_user_id_or_ip(request: Request) -> str:
    """Возвращает идентификатор пользователя из токена, либо IP-адрес."""
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        try:
            # Декодируем токен без проверки подписи/срока действия,
            # так как полноценная валидация происходит в get_current_user.
            payload = jwt.decode(
                token,
                config.secret_key,
                algorithms=[config.algorithm],
                options={"verify_signature": False, "verify_exp": False},
            )
            user_id = payload.get("sub")
            if user_id:
                return f"user:{user_id}"
        except JWTError:
            pass
    return get_remote_address(request)


# Создание экземпляра лимитера с определением клиента по user_id или IP
limiter = Limiter(
    key_func=get_user_id_or_ip,
    default_limits=[],
)
