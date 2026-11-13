"""Публичные HTML-страницы без авторизации: политика, соглашение, удаление аккаунта (Google Play / App Store)."""

from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse

from app.services.legal_pages import render_page, resolve_language

router = APIRouter()

# Страницы статичны: допускаем кэширование браузером и прокси; язык может зависеть от Accept-Language
_HEADERS = {"Cache-Control": "public, max-age=3600", "Vary": "Accept-Language"}


# Версия документа — дата вступления в силу (ISO); без параметра — текущая версия
_VERSION_QUERY = Query(
    None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Версия документа (дата вступления в силу, ГГГГ-ММ-ДД)"
)


def _page_response(page: str, lang: str | None, accept_language: str | None, version: str | None = None) -> HTMLResponse:
    """Собрать HTML-ответ страницы на выбранном языке и версии."""
    html = render_page(page, resolve_language(lang, accept_language), version)
    return HTMLResponse(content=html, headers=_HEADERS)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en"),
    version: str | None = _VERSION_QUERY,
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Политика конфиденциальности (ru, en); с `version` — текст прежней версии из архива."""
    return _page_response("privacy", lang, accept_language, version)


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en"),
    version: str | None = _VERSION_QUERY,
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Пользовательское соглашение (ru, en); с `version` — текст прежней версии из архива."""
    return _page_response("terms", lang, accept_language, version)


@router.get("/delete-account", response_class=HTMLResponse, include_in_schema=False)
async def delete_account_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en"),
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Порядок удаления аккаунта и данных (ru, en)."""
    return _page_response("delete-account", lang, accept_language)
