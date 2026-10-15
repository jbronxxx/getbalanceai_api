"""Типы содержимого публичных страниц."""

from typing import TypedDict

# Блок раздела: абзац (str) или маркированный список (list[str])
Block = str | list[str]
# Раздел: заголовок и набор блоков
Section = tuple[str, list[Block]]


class PageContent(TypedDict):
    """Содержимое одной страницы на одном языке."""

    title: str
    sections: list[Section]
