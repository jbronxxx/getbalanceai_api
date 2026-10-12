"""Эндпоинты управления бюджетами и лимитами расходов по категориям."""

import uuid

from fastapi import APIRouter, Depends, Header, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.exceptions import ErrorCode, NotFoundException
from app.models.models import Category, User
from app.schemas.schemas import (
    ApiResponse,
    BaseResponse,
    BudgetCreate,
    BudgetResponse,
    ErrorResponse,
)
from app.services.auth_service import get_current_user
from app.services.budget_service import BudgetService

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
    response_model=ApiResponse[BudgetResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Установить бюджет по категории",
)
async def create_budget(
    payload: BudgetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Установить лимит бюджета по определенной категории на месяц и год."""
    service = BudgetService(db)
    budget = await service.create(current_user.id, payload)
    return {"status": "success", "data": budget}


@router.get(
    "/",
    response_model=ApiResponse[list[BudgetResponse]],
    summary="Получить список бюджетов",
    responses={
        200: {
            "model": ApiResponse[list[BudgetResponse]],
            "description": "Список бюджетов с расчетом spent и remaining в базовой валюте пользователя",
        },
        304: {"description": "Данные не изменялись (ETag совпадает с заголовком If-None-Match)"},
    },
)
async def list_budgets(
    month: int | None = Query(default=None, ge=1, le=12, description="Фильтр по номеру месяца (1-12)"),
    year: int | None = Query(default=None, ge=2000, le=2100, description="Фильтр по году"),
    if_none_match: str | None = Header(
        None,
        alias="If-None-Match",
        description="ETag предыдущего ответа для условного кэширования",
    ),
    response: Response = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Получить список бюджетов с мультивалютным расчетом израсходованных средств и остатка лимита.

    Суммы фактически потраченных средств (spent) и доступного остатка (remaining) рассчитываются
    с автоматической конвертацией мультивалютных транзакций в базовую валюту пользователя.
    Поддерживает условное кэширование по ETag. При смене base_currency пользователя заголовок ETag
    автоматически инвалидируется.
    """
    service = BudgetService(db)

    current_etag = await service.get_etag(
        current_user.id,
        month=month,
        year=year,
        base_currency=current_user.base_currency,
    )

    if if_none_match and if_none_match.strip('"') == current_etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED)

    budgets = await service.get_all(
        current_user.id,
        month=month,
        year=year,
        user_base_currency=current_user.base_currency,
    )
    if response:
        response.headers["ETag"] = f'"{current_etag}"'

    return {"status": "success", "data": budgets}


@router.get(
    "/{budget_id}",
    response_model=ApiResponse[BudgetResponse],
    summary="Получить бюджет по ID",
)
async def get_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Получить информацию о бюджете по его уникальному ID."""
    service = BudgetService(db)
    budget = await service.get_by_id(current_user.id, budget_id)
    return {"status": "success", "data": budget}


@router.delete(
    "/{budget_id}",
    response_model=BaseResponse,
    summary="Удалить бюджет по ID",
)
async def delete_budget(
    budget_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Удалить запись бюджета по её уникальному идентификатору."""
    service = BudgetService(db)
    await service.delete(current_user.id, budget_id)
    return {"status": "success", "message": "Бюджет успешно удален"}


@router.delete(
    "/category/{category}/{month}/{year}",
    response_model=BaseResponse,
    summary="Удалить бюджет по категории, месяцу и году",
)
async def delete_budget_by_category_period(
    category: Category,
    month: int,
    year: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Удалить бюджет по названию категории, месяцу и году."""
    service = BudgetService(db)
    deleted = await service.delete_by_category_period(current_user.id, category, month, year)
    if not deleted:
        raise NotFoundException(
            code=ErrorCode.BUDGET_NOT_FOUND,
            message="Бюджет не найден",
            details={"category": str(category), "month": month, "year": year},
        )
    return {"status": "success", "message": "Бюджет успешно удален"}
