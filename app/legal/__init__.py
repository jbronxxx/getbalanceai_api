"""Тексты публичных юридических страниц (политика конфиденциальности, соглашение, удаление аккаунта)."""

from app.legal.content import PAGES
from app.legal.types import Block, PageContent, Section
from app.legal.ui import DEFAULT_LANGUAGE, LANGUAGES, SUPPORT_EMAIL, UI_STRINGS

__all__ = [
    "Block",
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "PAGES",
    "PageContent",
    "SUPPORT_EMAIL",
    "Section",
    "UI_STRINGS",
]
