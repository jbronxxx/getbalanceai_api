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
    """Возвращает идентификатор пользователя из токена, либо IP-адрес.

    sub берётся только из access-токена с проверенной подписью и сроком действия: иначе клиент
    подставлял бы произвольный sub в каждом запросе и получал новый счётчик, обходя лимит.
    IP-адрес клиента за nginx восстанавливается из X-Forwarded-For на уровне uvicorn (--forwarded-allow-ips).
    """
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ", 1)[1]
        try:
            payload = jwt.decode(token, config.secret_key, algorithms=[config.algorithm])
            user_id = payload.get("sub")
            if user_id and payload.get("type") == "access":
                return f"user:{user_id}"
        except JWTError:
            pass
    return get_remote_address(request)


# Создание экземпляра лимитера с определением клиента по user_id или IP.
# При недоступности хранилища (Redis) лимиты временно считаются в памяти воркера, а не роняют запросы
limiter = Limiter(
    key_func=get_user_id_or_ip,
    default_limits=[],
    storage_uri=config.rate_limit_storage_uri,
    in_memory_fallback_enabled=True,
)
