"""Эндпоинты регулярных платежей и доходов. Создание, правка и удаление — через пакетную синхронизацию /sync."""

from fastapi import APIRouter, Depends, Header, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.models import User
from app.routers.dependencies import not_modified_response
from app.schemas.schemas import ApiResponse, ErrorResponse, RecurringPaymentResponse
from app.services.auth_service import get_current_user
from app.services.recurring_payment_service import RecurringPaymentService

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {"model": ErrorResponse, "description": "Доступ запрещен"},
        500: {"model": ErrorResponse, "description": "Внутренняя ошибка сервера"},
    }
)


@router.get(
    "/",
    response_model=ApiResponse[list[RecurringPaymentResponse]],
    summary="Получить регулярные платежи",
    responses={
        200: {
            "model": ApiResponse[list[RecurringPaymentResponse]],
            "description": "Все регулярные платежи и доходы пользователя по дню месяца",
        },
        304: {"description": "Данные не изменялись (ETag совпадает с заголовком If-None-Match)"},
    },
)
async def list_recurring_payments(
    response: Response,
    if_none_match: str | None = Header(
        None,
        alias="If-None-Match",
        description="ETag предыдущего ответа для условного кэширования",
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Весь список регулярных платежей: клиент сверяет его с локальным (отсутствующий платёж удалён).

    Список короткий, поэтому без пагинации. Поддерживает условное кэширование по ETag (RFC 7232).
    """
    service = RecurringPaymentService(db)
    current_etag = await service.get_etag(current_user.id)
    not_modified = not_modified_response(if_none_match, current_etag)
    if not_modified is not None:
        return not_modified

    payments = await service.get_all(current_user.id)
    response.headers["ETag"] = f'"{current_etag}"'
    return {"status": "success", "data": payments}
