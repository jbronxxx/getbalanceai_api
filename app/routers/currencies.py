"""Эндпоинты получения справочника курсов валют."""

from fastapi import APIRouter, Depends, Request

from app.limiter import limiter
from app.schemas.schemas import ApiResponse, CurrencyRatesResponse, ErrorResponse
from app.services.currency_service import CurrencyService

router = APIRouter(
    responses={
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
    }
)


@router.get(
    "/rates",
    response_model=ApiResponse[CurrencyRatesResponse],
    summary="Получить актуальные курсы валют",
)
@limiter.limit("30/minute")
async def get_currency_rates(
    request: Request,
    currency_service: CurrencyService = Depends(),
):
    """Возвращает справочник актуальных курсов валют относительно USD.

    Курсы кэшируются в распределенном кэше Redis (TTL 24 часа).
    При сбое внешнего шлюза возвращаются кэшированные или резервные котировки.
    """
    rates = await currency_service.get_rates()
    return {"status": "success", "data": rates}
