"""Эндпоинты семейного Pro: семья, участники, приглашения по коду, выход и исключение."""

import uuid

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.limiter import limiter
from app.models.models import User
from app.schemas.schemas import (
    ApiResponse,
    BaseResponse,
    ErrorResponse,
    FamilyInviteCodeRequest,
    FamilyInviteCreatedResponse,
    FamilyInvitePreviewResponse,
    FamilyResponse,
)
from app.services.auth_service import get_consented_user
from app.services.family_service import FamilyService

# Попытки ввода кода приглашения (просмотр и вступление) — общий счётчик на пользователя
INVITE_CODE_ATTEMPTS = "10/hour"

router = APIRouter(
    responses={
        401: {"model": ErrorResponse, "description": "Требуется авторизация"},
        403: {
            "model": ErrorResponse,
            "description": "TERMS_NOT_ACCEPTED — не принята текущая версия соглашения; FAMILY_OWNER_ONLY — действие "
            "только для владельца; FAMILY_FROZEN — семья заморожена; FAMILY_PLAN_REQUIRED — нет семейного Pro",
        },
        404: {
            "model": ErrorResponse,
            "description": "FAMILY_NOT_FOUND — пользователь не в семье; FAMILY_INVITE_INVALID — код не найден, истёк "
            "или использован",
        },
        409: {
            "model": ErrorResponse,
            "description": "FAMILY_ALREADY_MEMBER — уже в семье; FAMILY_FULL — нет свободных мест",
        },
        422: {"model": ErrorResponse, "description": "Ошибка валидации входных данных"},
        429: {"model": ErrorResponse, "description": "Превышен лимит частоты запросов"},
    }
)


@router.get(
    "/me",
    response_model=ApiResponse[FamilyResponse | None],
    status_code=status.HTTP_200_OK,
    summary="Семья текущего пользователя",
)
async def get_my_family(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Семья с участниками и состоянием (заморожена или нет); data = null — пользователь не в семье.

    Email участников и действующие приглашения видит только владелец.
    """
    family = await FamilyService(db).get(current_user.id)
    return {"status": "success", "data": family}


@router.post(
    "/",
    response_model=ApiResponse[FamilyResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Создать семью",
)
@limiter.limit("10/minute")
async def create_family(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Создать семью, где текущий пользователь — владелец; нужен действующий семейный Pro."""
    family = await FamilyService(db).create(current_user.id)
    return {"status": "success", "data": family}


@router.post(
    "/me/invites",
    response_model=ApiResponse[FamilyInviteCreatedResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Создать приглашение",
)
@limiter.limit("10/day")
async def create_invite(
    request: Request,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Одноразовый код из 8 символов на 7 дней (только владелец). Код показывается один раз: владелец
    отправляет его через «Поделиться». Действующих приглашений — не больше свободных мест; не больше 10 в сутки."""
    invite = await FamilyService(db).create_invite(current_user.id)
    return {"status": "success", "data": invite}


@router.delete(
    "/me/invites/{invite_id}",
    response_model=BaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Отозвать приглашение",
)
async def revoke_invite(
    invite_id: uuid.UUID = Path(..., description="ID приглашения"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Отозвать действующее приглашение семьи (только владелец)."""
    await FamilyService(db).revoke_invite(current_user.id, invite_id)
    return {"status": "success", "message": "Invite revoked"}


@router.post(
    "/invites/preview",
    response_model=ApiResponse[FamilyInvitePreviewResponse],
    status_code=status.HTTP_200_OK,
    summary="Семья по коду приглашения",
)
@limiter.shared_limit(INVITE_CODE_ATTEMPTS, scope="family_invite_code")
async def preview_invite(
    request: Request,
    payload: FamilyInviteCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Имя владельца и число участников для экрана «Вступить в семью {владелец}». Код в теле, а не в URL:
    адреса попадают в журналы. Попыток ввода кода (вместе со вступлением) — не больше 10 в час."""
    preview = await FamilyService(db).preview_invite(payload.code)
    return {"status": "success", "data": preview}


@router.post(
    "/join",
    response_model=ApiResponse[FamilyResponse],
    status_code=status.HTTP_200_OK,
    summary="Вступить в семью по коду",
)
@limiter.shared_limit(INVITE_CODE_ATTEMPTS, scope="family_invite_code")
async def join_family(
    request: Request,
    payload: FamilyInviteCodeRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Вступить в семью после согласия «участники видят ваши общие записи, личные — нет». Свой личный Pro
    участника продолжает списываться в магазине — приложение предупреждает. После вступления клиент
    синхронизируется заново без since."""
    family = await FamilyService(db).join(current_user.id, payload.code)
    return {"status": "success", "data": family}


@router.delete(
    "/me/members/{user_id}",
    response_model=BaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Исключить участника",
)
async def remove_member(
    user_id: uuid.UUID = Path(..., description="ID участника"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Исключить участника (только владелец). Общие записи остаются в семье, транзакции, добавленные
    участником, копируются ему как личные."""
    await FamilyService(db).remove_member(current_user.id, user_id)
    return {"status": "success", "message": "Member removed"}


@router.post(
    "/me/leave",
    response_model=BaseResponse,
    status_code=status.HTTP_200_OK,
    summary="Выйти из семьи",
)
async def leave_family(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_consented_user),
):
    """Участник выходит: его транзакции из общих копируются ему как личные. Владелец выходит — семья
    распускается: общие записи достаются авторам как личные, участники теряют Pro владельца."""
    await FamilyService(db).leave(current_user.id)
    return {"status": "success", "message": "Left family"}
