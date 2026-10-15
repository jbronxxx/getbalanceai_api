"""Тесты публичных HTML-страниц: политика, соглашение, удаление аккаунта."""

import pytest
from httpx import AsyncClient

from app.legal import LANGUAGES, PAGES, SUPPORT_EMAIL, UI_STRINGS
from app.services.legal_pages import render_page, resolve_language

PAGE_PATHS = ("/privacy", "/terms", "/delete-account")


class TestResolveLanguage:
    """Выбор языка страницы по параметру и заголовку Accept-Language."""

    def test_query_param_has_priority(self):
        assert resolve_language("kk", "ru-RU,ru;q=0.9") == "kk"

    def test_query_param_is_normalized(self):
        assert resolve_language(" UZ ", None) == "uz"

    def test_unsupported_param_falls_back_to_header(self):
        assert resolve_language("de", "uz-UZ,uz;q=0.9") == "uz"

    def test_header_respects_quality(self):
        assert resolve_language(None, "de;q=1.0,ru;q=0.5,kk;q=0.8") == "kk"

    def test_header_with_invalid_quality_is_ignored(self):
        assert resolve_language(None, "kk;q=abc,ru;q=0.4") == "ru"

    def test_unsupported_everything_returns_default(self):
        assert resolve_language("zz", "fr-FR,de;q=0.8") == "en"

    def test_no_input_returns_default(self):
        assert resolve_language(None, None) == "en"


class TestLegalContent:
    """Целостность текстов: все страницы переведены на все языки."""

    @pytest.mark.parametrize("page", list(PAGES))
    def test_all_languages_present_with_same_structure(self, page: str):
        assert set(PAGES[page]) == set(LANGUAGES)
        sections_count = {len(PAGES[page][lang]["sections"]) for lang in LANGUAGES}
        assert len(sections_count) == 1

    def test_ui_strings_cover_all_languages_and_pages(self):
        for lang in LANGUAGES:
            for key in ("updated", "contact", "language", *PAGES):
                assert UI_STRINGS[lang][key]

    @pytest.mark.parametrize("lang", list(LANGUAGES))
    def test_privacy_states_operator_hosting_and_retention(self, lang: str):
        """Политика на каждом языке содержит оператора, контакт, расположение серверов и сроки хранения."""
        text = " ".join(
            block if isinstance(block, str) else " ".join(block)
            for _, blocks in PAGES["privacy"][lang]["sections"]
            for block in blocks
        )
        assert "Yevgeniy Makarov" in text
        assert SUPPORT_EMAIL in text
        assert "Helsinki" in text or "Хельсинки" in text or "Xelsinki" in text
        assert "7" in text and "30" in text
        assert "Crashlytics" not in text and "Sentry" not in text

    def test_support_email_is_project_domain(self):
        assert SUPPORT_EMAIL == "support@getbalanceai.app"

    def test_render_escapes_html_in_content(self, monkeypatch: pytest.MonkeyPatch):
        content = {"title": "<b>x</b>", "sections": [("<script>", ["<i>a</i>", ["<u>b</u>"]])]}
        monkeypatch.setitem(PAGES["privacy"], "en", content)
        html = render_page("privacy", "en")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<u>b</u>" not in html


class TestPublicPages:
    """HTTP-контракт публичных страниц."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PAGE_PATHS)
    @pytest.mark.parametrize("lang", list(LANGUAGES))
    async def test_page_is_public_html_in_requested_language(self, client: AsyncClient, path: str, lang: str):
        """Страница доступна без авторизации и отдаётся на языке из параметра lang."""
        response = await client.get(path, params={"lang": lang})
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/html")
        assert f'<html lang="{lang}">' in response.text
        assert f"mailto:{SUPPORT_EMAIL}" in response.text
        assert UI_STRINGS[lang]["updated"] in response.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PAGE_PATHS)
    async def test_language_from_accept_language_header(self, client: AsyncClient, path: str):
        """Без параметра lang язык берётся из Accept-Language."""
        response = await client.get(path, headers={"Accept-Language": "kk-KZ,kk;q=0.9,ru;q=0.5"})
        assert response.status_code == 200
        assert '<html lang="kk">' in response.text
        assert response.headers["vary"] == "Accept-Language"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PAGE_PATHS)
    async def test_default_language_is_english(self, client: AsyncClient, path: str):
        response = await client.get(path)
        assert response.status_code == 200
        assert '<html lang="en">' in response.text

    @pytest.mark.asyncio
    async def test_unsupported_lang_does_not_fail_or_reflect_input(self, client: AsyncClient):
        """Неподдерживаемый lang не ломает страницу и не попадает в разметку."""
        response = await client.get("/privacy", params={"lang": "<script>x"})
        assert response.status_code == 200
        assert "<script>x" not in response.text
        assert '<html lang="en">' in response.text

    @pytest.mark.asyncio
    async def test_too_long_lang_returns_422(self, client: AsyncClient):
        response = await client.get("/terms", params={"lang": "x" * 11})
        assert response.status_code == 422

    @pytest.mark.asyncio
    async def test_page_is_cacheable(self, client: AsyncClient):
        response = await client.get("/privacy", params={"lang": "ru"})
        assert response.headers["cache-control"] == "public, max-age=3600"

    @pytest.mark.asyncio
    async def test_pages_link_to_each_other_in_same_language(self, client: AsyncClient):
        response = await client.get("/privacy", params={"lang": "uz"})
        for path in PAGE_PATHS:
            assert f'href="{path}?lang=uz"' in response.text

    @pytest.mark.asyncio
    async def test_pages_are_hidden_from_openapi(self, client: AsyncClient):
        response = await client.get("/openapi.json")
        assert response.status_code == 200
        for path in PAGE_PATHS:
            assert path not in response.json()["paths"]

    @pytest.mark.asyncio
    async def test_unknown_page_returns_404(self, client: AsyncClient):
        response = await client.get("/imprint")
        assert response.status_code == 404
