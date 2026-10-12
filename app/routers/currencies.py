"""Эндпоинты получения справочника курсов валют."""

from fastapi import APIRouter, Depends, Request, Response

from app.limiter import limiter
from app.schemas.schemas import ApiResponse, CurrencyRatesResponse, ErrorResponse
from app.services.currency_service import CurrencyService
from config_reader.config_reader import config

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
    response: Response,
    currency_service: CurrencyService = Depends(),
):
    """Возвращает справочник актуальных курсов валют относительно USD.

    Курсы кэшируются в распределенном кэше Redis согласно системной конфигурации.
    При сбое внешнего шлюза возвращаются кэшированные или резервные котировки с флагом is_fallback=True.
    Возвращает заголовок Cache-Control для эффективного кэширования на стороне клиента.
    """
    rates = await currency_service.get_rates()
    response.headers["Cache-Control"] = f"public, max-age={config.currency_rates_cache_ttl}"
    return {"status": "success", "data": rates}
