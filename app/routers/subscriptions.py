"""Эндпоинты подписки: вебхук RevenueCat и обновление статуса после покупки."""

from fastapi import APIRouter, Depends, Header, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.schemas.schemas import (
    ApiResponse,
    BaseResponse,
    ErrorResponse,
    RevenueCatWebhook,
    SubscriptionStatus,
)
from app.services.auth_service import get_consented_user
from app.services.subscription_service import SubscriptionService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация (или неверный заголовок вебхука)"},
        422: {"model": ErrorResponse, "description": "Ошибка валидации данных"},
        503: {"model": ErrorResponse, "description": "RevenueCat не настроен или недоступен"},
    }
)


@router.post(
    "/refresh",
    response_model=ApiResponse[SubscriptionStatus],
    status_code=status.HTTP_200_OK,
    summary="Обновить статус подписки из RevenueCat",
    responses={
        403: {"model": ErrorResponse, "description": "TERMS_NOT_ACCEPTED — не принята текущая версия соглашения"},
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов"},
    },
)
@limiter.limit("10/minute")
async def refresh_subscription(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Запросить подписку пользователя в RevenueCat и сохранить её: приложение вызывает после покупки
    и восстановления покупок, не дожидаясь вебхука."""
    subscription = await SubscriptionService(db).sync(current_user.id)
    return {"status": "success", "data": subscription}


@router.post(
    "/webhooks/revenuecat",
    response_model=BaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Вебхук RevenueCat: изменение подписки покупателя",
)
async def revenuecat_webhook(
    payload: RevenueCatWebhook,
    authorization: str | None = Header(None, description="Значение, заданное в настройках вебхука RevenueCat"),
    db: AsyncSession = Depends(get_db),
):
    """Обновить подписку покупателей события из REST API RevenueCat.

    Ответ не 2xx RevenueCat повторяет: 503 — при сбое RevenueCat; неизвестный покупатель — 200 без изменений.
    """
    SubscriptionService.verify_webhook(authorization)
    synced = await SubscriptionService(db).handle_webhook(payload.event)
    return {"status": "success", "message": f"Synced {synced}"}
