"""Эндпоинты формирования персональной AI-аналитики и рекомендаций."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import CurrencyCode, User
from app.schemas.schemas import ApiResponse, ErrorResponse, InsightResponse
from app.services.ai_service import AIService
from app.services.auth_service import get_current_user

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        422: {"model": ErrorResponse, "description": "Неподдерживаемая валюта или некорректная локаль"},
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
    currency: CurrencyCode | None = Query(None, description="Валюта отчёта (по умолчанию базовая валюта профиля)"),
    # Значения попадают в промпт LLM и в ключ кэша: только код языка, без произвольного текста
    locale: str = Query(
        "ru",
        pattern=r"^[A-Za-z]{2}([_-][A-Za-z]{2})?$",
        description="Локаль пользователя (ru, uz, en, kk; допускается ru_RU)",
    ),
    generate: bool = Query(
        True,
        description="Разрешить генерацию через LLM; при false — только сохранённый отчёт или state=not_generated",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Вернуть AI-отчёт за месяц: сохранённый, если данные периода не менялись, иначе сгенерировать.

    Для будущего месяца и месяца без операций LLM не вызывается (`state=no_data`).
    """
    now = datetime.now(timezone.utc)
    target_month = month or now.month
    target_year = year or now.year

    effective_currency = currency.value if currency else (current_user.base_currency or "USD").strip().upper()
    service = AIService(db)
    insights = await service.get_insights(
        current_user.id,
        currency=effective_currency,
        locale=locale[:2].lower(),
        month=target_month,
        year=target_year,
        generate=generate,
    )
    return {"status": "success", "data": insights}
