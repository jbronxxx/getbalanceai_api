"""Тесты публичных HTML-страниц: политика, соглашение, удаление аккаунта."""

from datetime import date

import pytest
from httpx import AsyncClient

from app.legal import (
    ARCHIVE,
    LANGUAGES,
    LEGAL_DOCUMENTS,
    LEGAL_HISTORY,
    PAGES,
    SUPPORT_EMAIL,
    UI_STRINGS,
    LegalVersion,
    current_version,
    format_date,
)
from app.models.models import LEGAL_DOCUMENT_VALUES
from app.services.legal_pages import render_page, resolve_language

PAGE_PATHS = ("/privacy", "/terms", "/delete-account")


class TestResolveLanguage:
    """Выбор языка страницы по параметру и заголовку Accept-Language."""

    def test_query_param_has_priority(self):
        assert resolve_language("ru", "en-US,en;q=0.9") == "ru"

    def test_query_param_is_normalized(self):
        assert resolve_language(" RU ", None) == "ru"

    def test_unsupported_param_falls_back_to_header(self):
        assert resolve_language("de", "ru-RU,ru;q=0.9") == "ru"

    def test_header_respects_quality(self):
        assert resolve_language(None, "de;q=1.0,en;q=0.5,ru;q=0.8") == "ru"

    def test_header_with_invalid_quality_is_ignored(self):
        assert resolve_language(None, "en;q=abc,ru;q=0.4") == "ru"

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
        """Политика на каждом языке: оператор, контакт, серверы, сроки хранения, обработчики и GDPR; без пароля и KZ/UZ."""
        text = " ".join(
            block if isinstance(block, str) else " ".join(block)
            for _, blocks in PAGES["privacy"][lang]["sections"]
            for block in blocks
        )
        assert "Yevgeniy Makarov" in text
        assert SUPPORT_EMAIL in text
        assert "Helsinki" in text or "Хельсинки" in text
        assert "7" in text and "30" in text
        assert "Crashlytics" in text and "Sentry" not in text
        assert "open.er-api.com" in text
        assert "Cloudflare" in text and "R2" in text
        assert "GDPR" in text
        assert "парол" not in text.replace("Пароль от аккаунта Google", "")
        assert "password hash" not in text and "password hashing" not in text

    @pytest.mark.parametrize("lang", list(LANGUAGES))
    def test_terms_have_no_governing_law(self, lang: str):
        """Применимое право в соглашении не указано (решение 11.11.2026, MKT-01) — до проверки юристом."""
        titles = " ".join(title for title, _ in PAGES["terms"][lang]["sections"])
        text = " ".join(
            block if isinstance(block, str) else " ".join(block)
            for _, blocks in PAGES["terms"][lang]["sections"]
            for block in blocks
        )
        assert "Применимое право" not in titles and "Governing law" not in titles
        assert "Узбекистан" not in text and "Uzbekistan" not in text

    def test_support_email_is_project_domain(self):
        assert SUPPORT_EMAIL == "support@getbalanceai.app"

    def test_render_escapes_html_in_content(self, monkeypatch: pytest.MonkeyPatch):
        content = {"title": "<b>x</b>", "sections": [("<script>", ["<i>a</i>", ["<u>b</u>"]])]}
        monkeypatch.setitem(PAGES["privacy"], "en", content)
        html = render_page("privacy", "en")
        assert "<script>" not in html
        assert "&lt;script&gt;" in html
        assert "<u>b</u>" not in html


class TestLegalVersions:
    """Версии документов: даты, пункты «Что изменилось», архив прежних текстов."""

    def test_documents_match_model_check_constraint(self):
        """Документы с версиями — те же, что допускает CHECK user_consents.document."""
        assert set(LEGAL_DOCUMENTS) == set(LEGAL_DOCUMENT_VALUES)
        assert all(document in PAGES for document in LEGAL_DOCUMENTS)

    @pytest.mark.parametrize("document", list(LEGAL_HISTORY))
    def test_history_is_ordered_and_translated(self, document: str):
        """Версии идут по возрастанию даты; «Что изменилось» — на всех языках, 1–3 пункта."""
        history = LEGAL_HISTORY[document]
        assert [v.effective for v in history] == sorted({v.effective for v in history})
        for version in history:
            assert set(version.changes) == set(LANGUAGES)
            assert all(1 <= len(items) <= 3 for items in version.changes.values())

    @pytest.mark.parametrize("document", list(LEGAL_HISTORY))
    def test_archive_has_every_previous_version(self, document: str):
        """Каждая прежняя версия лежит в архиве на всех языках — по записи о согласии можно показать её текст."""
        previous = {v.version for v in LEGAL_HISTORY[document][:-1]}
        assert set(ARCHIVE[document]) == previous
        for texts in ARCHIVE[document].values():
            assert set(texts) == set(LANGUAGES)

    def test_format_date(self):
        assert format_date(date(2026, 11, 13), "ru") == "13 ноября 2026 г."
        assert format_date(date(2026, 1, 5), "en") == "January 5, 2026"

    @pytest.mark.asyncio
    async def test_current_version_by_parameter(self, client: AsyncClient):
        version = current_version("terms").version
        response = await client.get("/terms", params={"lang": "en", "version": version})
        assert response.status_code == 200
        assert PAGES["terms"]["en"]["title"] in response.text

    @pytest.mark.asyncio
    async def test_archived_version_shows_old_text_and_date(self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch):
        """Прежняя версия из архива: её текст и её дата; ссылки на другой язык остаются на этой версии."""
        old = LegalVersion(effective=date(2026, 1, 1), changes={lang: ["-"] for lang in LANGUAGES})
        monkeypatch.setitem(LEGAL_HISTORY, "terms", (old, *LEGAL_HISTORY["terms"]))
        old_text = {lang: {"title": f"Old terms {lang}", "sections": [("1", ["old"])]} for lang in LANGUAGES}
        monkeypatch.setitem(ARCHIVE, "terms", {old.version: old_text})

        response = await client.get("/terms", params={"lang": "en", "version": "2026-01-01"})
        assert response.status_code == 200
        assert "Old terms en" in response.text
        assert "January 1, 2026" in response.text
        assert "?lang=ru&amp;version=2026-01-01" in response.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("path", "version", "status"),
        [
            ("/terms", "2000-01-01", 404),
            ("/privacy", "2000-01-01", 404),
            ("/terms", "latest", 422),
            ("/delete-account", "2026-11-13", 200),
        ],
    )
    async def test_unknown_or_invalid_version(self, client: AsyncClient, path: str, version: str, status: int):
        """Неизвестная версия — 404, не дата — 422; у страницы удаления версий нет, параметр не принимается."""
        response = await client.get(path, params={"version": version})
        assert response.status_code == status
        if path == "/delete-account":
            assert 'class="meta"' not in response.text


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
        page = path.lstrip("/")
        if page in LEGAL_HISTORY:
            effective = format_date(current_version(page).effective, lang)
            assert UI_STRINGS[lang]["updated"].format(date=effective) in response.text
        else:
            assert 'class="meta"' not in response.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("path", PAGE_PATHS)
    async def test_language_from_accept_language_header(self, client: AsyncClient, path: str):
        """Без параметра lang язык берётся из Accept-Language."""
        response = await client.get(path, headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.5"})
        assert response.status_code == 200
        assert '<html lang="ru">' in response.text
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
        response = await client.get("/privacy", params={"lang": "ru"})
        for path in PAGE_PATHS:
            assert f'href="{path}?lang=ru"' in response.text

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
