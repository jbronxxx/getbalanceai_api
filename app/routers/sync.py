"""Эндпоинт пакетной синхронизации оффлайн данных транзакций и бюджетов."""

from datetime import tzinfo

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.routers.dependencies import get_client_timezone
from app.schemas.schemas import ApiResponse, ErrorResponse, SyncPayload, SyncResponse
from app.services.auth_service import get_current_user
from app.services.sync_service import SyncService

router = APIRouter(
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Ошибка валидации или параметров синхронизации",
        },
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        422: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов (60/мин на пользователя)"},
        500: {
            "model": ErrorResponse,
            "description": "Внутренняя ошибка сервера при сохранении данных",
        },
    }
)


@router.post(
    "/",
    response_model=ApiResponse[SyncResponse],
    status_code=status.HTTP_200_OK,
    summary="Синхронизировать данные",
    description=(
        "Пакетная синхронизация транзакций и бюджетов. Выполняет идемпотентный UPSERT "
        "для бюджетов по ID или составному ключу (category + month + year) без дубликатов "
        "и без нарушения уникальных ограничений базы данных. Конфликтующие записи возвращаются в rejected."
    ),
)
@limiter.limit("60/minute")
async def sync_data(
    request: Request,
    payload: SyncPayload,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
    tz: tzinfo = Depends(get_client_timezone),
):
    """Применить батч оффлайн-изменений клиента; отклонённые записи возвращаются в rejected."""
    result = await SyncService(db).sync(current_user, payload, tz)
    return {"status": "success", "data": result}
