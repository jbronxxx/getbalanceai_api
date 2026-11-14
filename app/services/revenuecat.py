"""Клиент REST API RevenueCat (v1): статус доступа Pro (и семейного Pro) покупателя и удаление покупателя.

Покупатель RevenueCat (app_user_id) — id аккаунта бэкенда: приложение вызывает `logIn` после входа.
Секретный ключ (`REVENUECAT_API_KEY`) в журналы не пишется.
"""

from dataclasses import dataclass
from datetime import datetime

import httpx

from config_reader.config_reader import config


class RevenueCatError(Exception):
    """RevenueCat недоступен или ответил ошибкой: статус подписки не получен или покупатель не удалён."""


@dataclass(frozen=True)
class ProEntitlement:
    """Доступ Pro покупателя по данным RevenueCat."""

    expires_at: datetime | None  # None — бессрочный доступ
    will_renew: bool
    is_trial: bool
    product_id: str
    store: str
    family: bool = False  # Есть право семейного Pro (entitlement `family`)
    family_expires_at: datetime | None = None  # Окончание права семейного Pro; None при family — бессрочно


def _parse_time(value: str | None) -> datetime | None:
    """Время RevenueCat в ISO 8601 (`2026-12-14T10:00:00Z`) → datetime с поясом; пусто — None."""
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _entitlement_expiry(entitlement: dict) -> datetime | None:
    """Срок доступа — позднейшее из окончания периода и окончания льготного периода магазина (grace period)."""
    expires_at = _parse_time(entitlement.get("expires_date"))
    grace_until = _parse_time(entitlement.get("grace_period_expires_date"))
    if expires_at is not None and grace_until is not None:
        expires_at = max(expires_at, grace_until)
    return expires_at


def parse_pro_entitlement(
    subscriber: dict, entitlement_id: str, family_entitlement_id: str | None = None
) -> ProEntitlement | None:
    """Доступ Pro из объекта `subscriber` ответа GET /subscribers; доступа нет — None.

    Пока магазин пытается списать оплату (grace period), доступ сохраняется. Истёкший доступ тоже возвращается:
    тариф считается по сроку. Право семейного Pro (`family_entitlement_id`) читается вместе с Pro: семейный
    продукт даёт оба; после перехода на личный Pro право семьи истекает, а Pro остаётся.
    """
    entitlements = subscriber.get("entitlements") or {}
    entitlement = entitlements.get(entitlement_id)
    if not isinstance(entitlement, dict):
        return None
    product_id = str(entitlement.get("product_identifier") or "")
    expires_at = _entitlement_expiry(entitlement)
    family = entitlements.get(family_entitlement_id) if family_entitlement_id else None
    subscription = (subscriber.get("subscriptions") or {}).get(product_id) or {}
    return ProEntitlement(
        expires_at=expires_at,
        # Продление ждёт только подписка со сроком, у которой магазин не сообщил об отмене
        will_renew=expires_at is not None and bool(subscription) and not subscription.get("unsubscribe_detected_at"),
        is_trial=subscription.get("period_type") == "trial",
        product_id=product_id[:100],
        store=str(subscription.get("store") or "unknown")[:30],
        family=isinstance(family, dict),
        family_expires_at=_entitlement_expiry(family) if isinstance(family, dict) else None,
    )


class RevenueCatClient:
    """Запросы к REST API RevenueCat с секретным ключом; один пул соединений на процесс."""

    def __init__(self) -> None:
        self._http: httpx.AsyncClient | None = None

    def _client(self) -> httpx.AsyncClient:
        """Переиспользуемый HTTP-клиент с базовым адресом и ключом."""
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(
                base_url=config.revenuecat_api_url,
                timeout=config.revenuecat_timeout,
                headers={"Authorization": f"Bearer {config.revenuecat_api_key}"},
            )
        return self._http

    async def close(self) -> None:
        """Закрыть пул соединений при завершении работы."""
        if self._http is not None and not self._http.is_closed:
            await self._http.aclose()
        self._http = None

    async def get_pro_entitlement(self, app_user_id: str) -> ProEntitlement | None:
        """Доступ Pro покупателя; нет доступа — None. Сбой сети или ответ не 2xx — `RevenueCatError`."""
        try:
            response = await self._client().get(f"/subscribers/{app_user_id}")
            response.raise_for_status()
            subscriber = response.json()["subscriber"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as err:
            raise RevenueCatError(f"Не удалось получить покупателя RevenueCat: {type(err).__name__}: {err}") from err
        return parse_pro_entitlement(subscriber, config.revenuecat_pro_entitlement, config.revenuecat_family_entitlement)

    async def delete_subscriber(self, app_user_id: str) -> None:
        """Удалить покупателя и историю его покупок в RevenueCat (подписку в магазине это не отменяет).

        Покупателя нет (404) — удалять нечего. Сбой сети или другой ответ не 2xx — `RevenueCatError`.
        """
        try:
            response = await self._client().delete(f"/subscribers/{app_user_id}")
            if response.status_code == httpx.codes.NOT_FOUND:
                return
            response.raise_for_status()
        except httpx.HTTPError as err:
            raise RevenueCatError(f"Не удалось удалить покупателя RevenueCat: {type(err).__name__}: {err}") from err


def revenuecat_configured() -> bool:
    """Задан ли секретный ключ REST API RevenueCat."""
    return bool(config.revenuecat_api_key)


_client: RevenueCatClient | None = None


def get_revenuecat_client() -> RevenueCatClient:
    """Единый клиент RevenueCat процесса."""
    global _client
    if _client is None:
        _client = RevenueCatClient()
    return _client


async def close_revenuecat_client() -> None:
    """Закрыть пул соединений клиента RevenueCat при завершении работы приложения."""
    if _client is not None:
        await _client.close()
