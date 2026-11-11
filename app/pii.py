"""Шифрование персональных данных пользователя в БД: имя и email.

* Значение шифруется AES-256-GCM со случайным nonce (12 байт). Формат в колонке — `<id ключа>:<base64(nonce + шифртекст)>`:
  id ключа позволяет расшифровывать старые строки после ротации, пока старый ключ остаётся в `PII_ENCRYPTION_KEYS`.
  В связанные данные (AAD) GCM входит имя колонки: шифртекст email нельзя подставить в колонку имени.
* Поиск по email — по «слепому индексу» `HMAC-SHA256(PII_HMAC_KEY, email в нижнем регистре)`: шифртекст каждый раз
  разный, сравнивать его в SQL нельзя. Индекс уникален — один адрес даёт один аккаунт.
* Ключи живут только в окружении сервера, в БД и её резервных копиях их нет.
"""

import base64
import binascii
import hashlib
import hmac
import os
import re
from dataclasses import dataclass
from functools import lru_cache

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from config_reader.config_reader import config

KEY_BYTES = 32
NONCE_BYTES = 12
KEY_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,16}$")


class PiiKeyError(ValueError):
    """Ключи шифрования персональных данных заданы неверно."""


class PiiDecryptError(ValueError):
    """Значение из БД не расшифровывается: нет ключа с таким id, другой ключ или повреждённые данные."""


def _decode_key(value: str, name: str) -> bytes:
    """Ключ из base64 строго на 32 байта (AES-256 / HMAC-SHA256)."""
    try:
        key = base64.b64decode(value.strip(), validate=True)
    except (binascii.Error, ValueError) as err:
        raise PiiKeyError(f"{name}: ключ не в base64") from err
    if len(key) != KEY_BYTES:
        raise PiiKeyError(f"{name}: ключ должен быть {KEY_BYTES} байта, получено {len(key)}")
    return key


def parse_encryption_keys(entries: list[str]) -> tuple[str, dict[str, bytes]]:
    """Разобрать `PII_ENCRYPTION_KEYS` (`id:base64`, через запятую): вернуть id текущего ключа и все ключи.

    Первый ключ — текущий, им шифруются новые значения; остальные нужны только для расшифровки строк до ротации.
    """
    if not entries:
        raise PiiKeyError("PII_ENCRYPTION_KEYS не задан")
    keys: dict[str, bytes] = {}
    for entry in entries:
        key_id, sep, value = entry.partition(":")
        if not sep or not KEY_ID_PATTERN.match(key_id):
            raise PiiKeyError("PII_ENCRYPTION_KEYS: ожидается <id>:<base64-ключ>, id — латиница, цифры, _ или -")
        if key_id in keys:
            raise PiiKeyError(f"PII_ENCRYPTION_KEYS: id ключа {key_id} повторяется")
        keys[key_id] = _decode_key(value, f"PII_ENCRYPTION_KEYS[{key_id}]")
    return entries[0].partition(":")[0], keys


def normalize_email(email: str) -> str:
    """Email для слепого индекса: без пробелов по краям, в нижнем регистре (как хранится)."""
    return email.strip().lower()


@dataclass(frozen=True)
class PiiCipher:
    """Шифрование значений колонок и слепой индекс email на заданных ключах."""

    current_key_id: str
    keys: dict[str, bytes]
    hmac_key: bytes

    def encrypt(self, plaintext: str, context: str) -> str:
        """Зашифровать текущим ключом; `context` — имя колонки (AAD)."""
        nonce = os.urandom(NONCE_BYTES)
        sealed = AESGCM(self.keys[self.current_key_id]).encrypt(nonce, plaintext.encode("utf-8"), context.encode())
        return f"{self.current_key_id}:{base64.b64encode(nonce + sealed).decode('ascii')}"

    def decrypt(self, token: str, context: str) -> str:
        """Расшифровать значение любым ключом из списка по id в префиксе."""
        key_id, sep, payload = token.partition(":")
        key = self.keys.get(key_id)
        if not sep or key is None:
            raise PiiDecryptError(f"Нет ключа шифрования с id «{key_id}» для колонки {context}")
        try:
            raw = base64.b64decode(payload, validate=True)
            plaintext = AESGCM(key).decrypt(raw[:NONCE_BYTES], raw[NONCE_BYTES:], context.encode())
        except (binascii.Error, ValueError, InvalidTag) as err:
            raise PiiDecryptError(f"Значение колонки {context} не расшифровывается ключом «{key_id}»") from err
        return plaintext.decode("utf-8")

    @staticmethod
    def key_id_of(token: str) -> str:
        """Id ключа, которым зашифровано значение (для ротации)."""
        return token.partition(":")[0]

    def blind_index(self, email: str) -> str:
        """HMAC-SHA256 нормализованного email в hex (64 символа)."""
        return hmac.new(self.hmac_key, normalize_email(email).encode("utf-8"), hashlib.sha256).hexdigest()


def cipher_from_settings(encryption_keys: list[str], hmac_key: str) -> PiiCipher:
    """Собрать шифр из значений настроек; ошибка формата — `PiiKeyError`."""
    current_key_id, keys = parse_encryption_keys(encryption_keys)
    return PiiCipher(current_key_id=current_key_id, keys=keys, hmac_key=_decode_key(hmac_key, "PII_HMAC_KEY"))


@lru_cache(maxsize=1)
def get_pii_cipher() -> PiiCipher:
    """Шифр из конфигурации приложения (один на процесс)."""
    return cipher_from_settings(config.pii_encryption_keys, config.pii_hmac_key)


def blind_index(email: str) -> str:
    """Слепой индекс email на ключе из конфигурации."""
    return get_pii_cipher().blind_index(email)
