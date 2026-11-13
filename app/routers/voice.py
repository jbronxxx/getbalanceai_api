"""Эндпоинты голосового ввода: разбор распознанной фразы на черновики записей."""

from datetime import tzinfo

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.routers.dependencies import get_client_timezone
from app.schemas.schemas import (
    ApiResponse,
    ErrorResponse,
    VoiceParseRequest,
    VoiceParseResponse,
)
from app.services.auth_service import get_consented_user
from app.services.voice_service import VoiceParseService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "TERMS_NOT_ACCEPTED — не принята текущая версия соглашения"},
        422: {"model": ErrorResponse, "description": "Пустой или слишком длинный текст, неподдерживаемая валюта"},
        429: {
            "model": ErrorResponse,
            "description": "AI_QUOTA_EXCEEDED — суточная квота разборов исчерпана; RATE_LIMIT_EXCEEDED — частота",
        },
        503: {"model": ErrorResponse, "description": "Модель или Redis недоступны, ключи LLM не заданы"},
    }
)


@router.post(
    "/parse",
    response_model=ApiResponse[VoiceParseResponse],
    status_code=status.HTTP_200_OK,
    summary="Разобрать фразу голосового ввода на записи",
)
@limiter.limit("20/minute")
async def parse_voice(
    request: Request,
    payload: VoiceParseRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
    tz: tzinfo = Depends(get_client_timezone),
):
    """Вернуть черновики записей из фразы, распознанной на устройстве; сервер ничего не сохраняет.

    «Сегодня» и «вчера» считаются по часам клиента (заголовок X-Timezone-Offset). Пустой список — в фразе
    нет ни одной суммы. Квота — `VOICE_DAILY_LIMIT_PER_USER` разборов в сутки (UTC); неудачный разбор
    квоту не расходует.
    """
    result = await VoiceParseService(db).parse(
        current_user.id,
        text=payload.text,
        locale=payload.locale[:2].lower(),
        currency=payload.currency,
        tz=tz,
    )
    return {"status": "success", "data": result}
