"""Реестр текстов публичных страниц."""

from app.legal.delete_account import DELETE_ACCOUNT
from app.legal.privacy import PRIVACY
from app.legal.terms import TERMS
from app.legal.types import PageContent

# Реестр: slug страницы -> язык -> содержимое
PAGES: dict[str, dict[str, PageContent]] = {
    "privacy": PRIVACY,
    "terms": TERMS,
    "delete-account": DELETE_ACCOUNT,
}
