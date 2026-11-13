"""Тексты публичных юридических страниц (политика конфиденциальности, соглашение, удаление аккаунта) и их версии."""

from app.legal.archive import ARCHIVE
from app.legal.content import PAGES
from app.legal.types import Block, PageContent, Section
from app.legal.ui import (
    DEFAULT_LANGUAGE,
    LANGUAGES,
    SUPPORT_EMAIL,
    UI_STRINGS,
    format_date,
)
from app.legal.versions import (
    LEGAL_DOCUMENTS,
    LEGAL_HISTORY,
    LegalVersion,
    current_version,
)

__all__ = [
    "ARCHIVE",
    "Block",
    "DEFAULT_LANGUAGE",
    "LANGUAGES",
    "LEGAL_DOCUMENTS",
    "LEGAL_HISTORY",
    "LegalVersion",
    "PAGES",
    "PageContent",
    "SUPPORT_EMAIL",
    "Section",
    "UI_STRINGS",
    "current_version",
    "format_date",
]
