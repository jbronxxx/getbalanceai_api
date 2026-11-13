"""Эндпоинты юридических документов: текущие версии с пунктами «Что изменилось» и их принятие."""

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.legal import current_version
from app.limiter import limiter
from app.models.models import User
from app.schemas.schemas import (
    ApiResponse,
    ErrorResponse,
    LegalAcceptRequest,
    LegalDocumentInfo,
    LegalVersionsResponse,
    UserResponse,
)
from app.services.auth_service import get_current_user
from app.services.consent_service import ConsentService
from app.services.legal_pages import resolve_language
from logger.request_log import client_info

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        409: {
            "model": ErrorResponse,
            "description": "LEGAL_VERSION_OUTDATED — принимаемая версия уже не текущая (в details — current_version)",
        },
        422: {"model": ErrorResponse, "description": "Ошибка валидации данных"},
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов"},
    }
)


def _document_info(document: str, lang: str) -> LegalDocumentInfo:
    """Текущая версия документа с пунктами «Что изменилось» на языке `lang`."""
    version = current_version(document)
    return LegalDocumentInfo(version=version.version, effective_date=version.effective, changes=version.changes[lang])


@router.get(
    "",
    response_model=ApiResponse[LegalVersionsResponse],
    summary="Текущие версии соглашения и политики",
)
async def legal_versions(
    lang: str | None = Query(None, max_length=10, description="Язык пунктов «Что изменилось»: ru, en"),
    x_app_language: str | None = Header(None),
    accept_language: str | None = Header(None),
) -> dict[str, str | LegalVersionsResponse]:
    """Текущие версии документов и что в них изменилось — для экрана новой версии и уведомления о политике.

    Язык: параметр `lang`, затем язык приложения (X-App-Language), затем Accept-Language, иначе `en`.
    """
    language = resolve_language(lang or x_app_language, accept_language)
    data = LegalVersionsResponse(terms=_document_info("terms", language), privacy=_document_info("privacy", language))
    return {"status": "success", "data": data}


@router.post(
    "/accept",
    response_model=ApiResponse[UserResponse],
    summary="Принять версии соглашения и политики",
)
@limiter.limit("10/minute")
async def accept_legal(
    request: Request,
    payload: LegalAcceptRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> dict[str, str | User]:
    """Записать принятие показанных пользователю версий; доступно и без принятой текущей версии соглашения.

    Версия должна быть текущей, иначе 409 LEGAL_VERSION_OUTDATED. Ответ — профиль с обновлённым `legal`.
    """
    await ConsentService(db).accept(current_user, payload.versions(), client_info(request))
    return {"status": "success", "data": current_user}
