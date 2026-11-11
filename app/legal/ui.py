"""Общие строки интерфейса публичных страниц: языки, контакт, подписи навигации."""

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
        "updated": "Дата вступления в силу: 11 ноября 2026 г.",
        "contact": "Связаться с нами",
        "privacy": "Политика конфиденциальности",
        "terms": "Пользовательское соглашение",
        "delete-account": "Удаление аккаунта",
        "language": "Язык",
    },
    "en": {
        "updated": "Effective date: November 11, 2026",
        "contact": "Contact us",
        "privacy": "Privacy Policy",
        "terms": "Terms of Service",
        "delete-account": "Delete account",
        "language": "Language",
    },
}
