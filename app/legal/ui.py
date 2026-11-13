"""Общие строки интерфейса публичных страниц: языки, контакт, подписи навигации, формат даты."""

from datetime import date

# Контактный адрес для запросов по данным и поддержке
SUPPORT_EMAIL = "support@getbalanceai.app"

# Язык по умолчанию, если ни параметр lang, ни Accept-Language не дали поддерживаемого значения
DEFAULT_LANGUAGE = "en"

# Поддерживаемые языки (код -> название на самом языке)
LANGUAGES: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
}

UI_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "updated": "Дата вступления в силу: {date}",
        "contact": "Связаться с нами",
        "privacy": "Политика конфиденциальности",
        "terms": "Пользовательское соглашение",
        "delete-account": "Удаление аккаунта",
        "language": "Язык",
    },
    "en": {
        "updated": "Effective date: {date}",
        "contact": "Contact us",
        "privacy": "Privacy Policy",
        "terms": "Terms of Service",
        "delete-account": "Delete account",
        "language": "Language",
    },
}

# Названия месяцев в дате вступления в силу (в русском — родительный падеж)
_MONTHS: dict[str, tuple[str, ...]] = {
    "ru": (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    ),
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
}


def format_date(value: date, lang: str) -> str:
    """Дата прописью на языке страницы: «13 ноября 2026 г.», «November 13, 2026»."""
    month = _MONTHS[lang][value.month - 1]
    if lang == "ru":
        return f"{value.day} {month} {value.year} г."
    return f"{month} {value.day}, {value.year}"
