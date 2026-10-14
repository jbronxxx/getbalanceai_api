"""Эндпоинты формирования персональной AI-аналитики и рекомендаций."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.schemas.schemas import ApiResponse, ErrorResponse, InsightResponse
from app.services.ai_service import AIService
from app.services.auth_service import get_current_user

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
    }
)


@router.get(
    "/",
    response_model=ApiResponse[InsightResponse],
    summary="Получить AI-рекомендации по расходам",
)
@limiter.limit("10/minute")
async def get_insights(
    request: Request,
    month: int | None = Query(None, ge=1, le=12, description="Месяц анализа (по умолчанию текущий)"),
    year: int | None = Query(None, ge=2000, le=2100, description="Год анализа (по умолчанию текущий)"),
    currency: str | None = Query(None, description="Валюта пользователя (по умолчанию базовая валюта профиля)"),
    locale: str = Query("ru", description="Локаль пользователя (ru, uz, en, kk)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Сформировать персонализированные финансовые советы на основе истории транзакций и бюджетов пользователя."""
    now = datetime.now(timezone.utc)
    target_month = month or now.month
    target_year = year or now.year

    effective_currency = (currency or getattr(current_user, "base_currency", None) or "USD").strip().upper()
    service = AIService(db)
    insights = await service.get_insights(
        current_user.id,
        currency=effective_currency,
        locale=locale,
        month=target_month,
        year=target_year,
    )
    return {"status": "success", "data": insights}
