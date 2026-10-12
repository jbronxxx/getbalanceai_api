"""Эндпоинты управления финансовыми транзакциями (доходы и расходы)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User
from app.schemas.schemas import (
    ApiResponse,
    BaseResponse,
    ErrorResponse,
    PaginatedResponse,
    TransactionCreate,
    TransactionResponse,
)
from app.services.auth_service import get_current_user
from app.services.transaction_service import TransactionService

router = APIRouter(
    responses={
        400: {"model": ErrorResponse, "description": "Ошибка бизнес-логики"},
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        404: {"model": ErrorResponse, "description": "Ресурс не найден"},
        422: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
    }
)


@router.post(
    "/",
    response_model=ApiResponse[TransactionResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Добавить новую транзакцию",
)
async def create_transaction(
    payload: TransactionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Создать новую транзакцию (доход или расход) для авторизованного пользователя."""
    service = TransactionService(db)
    transaction = await service.create(
        current_user.id,
        payload,
        user_base_currency=current_user.base_currency,
    )
    return {"status": "success", "data": transaction}


@router.get(
    "/",
    response_model=ApiResponse[PaginatedResponse[TransactionResponse]],
    summary="Получить список транзакций",
)
async def list_transactions(
    limit: int = Query(50, ge=1, le=100, description="Количество транзакций на странице (макс. 100)"),
    cursor: str | None = Query(None, description="Курсор для получения следующей страницы"),
    since: datetime | None = None,
    if_none_match: str | None = Header(None, alias="If-None-Match"),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Получить пагинированный список транзакций пользователя с поддержкой ETag-кэширования."""
    service = TransactionService(db)

    current_etag = await service.get_etag(current_user.id, since=since, limit=limit, cursor=cursor)

    if if_none_match and if_none_match.strip('"') == current_etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    items, has_more, next_cursor = await service.get_all(current_user.id, since=since, limit=limit, cursor=cursor)
    if response:
        response.headers["ETag"] = f'"{current_etag}"'

    return {
        "status": "success",
        "data": {
            "items": items,
            "has_more": has_more,
            "next_cursor": next_cursor,
            "limit": limit,
        },
    }


@router.delete(
    "/{transaction_id}",
    response_model=BaseResponse,
    summary="Удалить транзакцию",
)
async def delete_transaction(
    transaction_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Удалить запись транзакции по её идентификатору."""
    service = TransactionService(db)
    await service.delete(current_user.id, transaction_id)
    return {"status": "success", "message": "Transaction deleted successfully"}
