"""Эндпоинты синхронизации: пакетная выгрузка оффлайн-изменений и выборка изменений с сервера."""

from datetime import datetime, tzinfo

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.routers.dependencies import get_client_timezone
from app.schemas.schemas import (
    ApiResponse,
    ErrorResponse,
    SyncChangesResponse,
    SyncPayload,
    SyncResponse,
)
from app.services.auth_service import get_consented_user
from app.services.sync_service import SyncService

router = APIRouter(
    responses={
        400: {
            "model": ErrorResponse,
            "description": "Ошибка валидации или параметров синхронизации",
        },
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "TERMS_NOT_ACCEPTED — не принята текущая версия соглашения"},
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
    current_user: User = Depends(get_consented_user),
    tz: tzinfo = Depends(get_client_timezone),
):
    """Применить батч оффлайн-изменений клиента; отклонённые записи возвращаются в rejected."""
    result = await SyncService(db).sync(current_user, payload, tz)
    return {"status": "success", "data": result}


@router.get(
    "/changes",
    response_model=ApiResponse[SyncChangesResponse],
    status_code=status.HTTP_200_OK,
    summary="Изменения транзакций с прошлой синхронизации",
    description=(
        "Транзакции, созданные или изменённые после since, и ID транзакций, удалённых после since. "
        "Без since — все транзакции пользователя. Страницы по (updated_at, id); удалённые — только на первой странице. "
        "server_time первой страницы клиент передаёт как since в следующий раз."
    ),
)
@limiter.limit("120/minute")
async def get_changes(
    request: Request,
    since: datetime | None = Query(None, description="server_time первой страницы прошлой синхронизации (ISO 8601)"),
    cursor: str | None = Query(None, max_length=512, description="next_cursor предыдущей страницы"),
    limit: int = Query(200, ge=1, le=500, description="Размер страницы (макс. 500)"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Отдать изменения транзакций пользователя для инкрементальной синхронизации устройства."""
    result = await SyncService(db).get_changes(current_user.id, since=since, cursor=cursor, limit=limit)
    return {"status": "success", "data": result}
