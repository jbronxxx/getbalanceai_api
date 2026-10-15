"""Публичные HTML-страницы без авторизации: политика, соглашение, удаление аккаунта (Google Play / App Store)."""

from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse

from app.services.legal_pages import render_page, resolve_language

router = APIRouter()

# Страницы статичны: допускаем кэширование браузером и прокси; язык может зависеть от Accept-Language
_HEADERS = {"Cache-Control": "public, max-age=3600", "Vary": "Accept-Language"}


def _page_response(page: str, lang: str | None, accept_language: str | None) -> HTMLResponse:
    """Собрать HTML-ответ страницы на выбранном языке."""
    html = render_page(page, resolve_language(lang, accept_language))
    return HTMLResponse(content=html, headers=_HEADERS)


@router.get("/privacy", response_class=HTMLResponse, include_in_schema=False)
async def privacy_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en, uz, kk"),
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Политика конфиденциальности (ru, en, uz, kk)."""
    return _page_response("privacy", lang, accept_language)


@router.get("/terms", response_class=HTMLResponse, include_in_schema=False)
async def terms_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en, uz, kk"),
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Пользовательское соглашение (ru, en, uz, kk)."""
    return _page_response("terms", lang, accept_language)


@router.get("/delete-account", response_class=HTMLResponse, include_in_schema=False)
async def delete_account_page(
    lang: str | None = Query(None, max_length=10, description="Язык страницы: ru, en, uz, kk"),
    accept_language: str | None = Header(None),
) -> HTMLResponse:
    """Порядок удаления аккаунта и данных (ru, en, uz, kk)."""
    return _page_response("delete-account", lang, accept_language)
