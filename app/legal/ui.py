"""Общие строки интерфейса публичных страниц: языки, контакт, подписи навигации."""

# Контактный адрес для запросов по данным и поддержке
SUPPORT_EMAIL = "support@getbalanceai.app"

# Язык по умолчанию, если ни параметр lang, ни Accept-Language не дали поддерживаемого значения
DEFAULT_LANGUAGE = "en"

# Поддерживаемые языки (код -> название на самом языке)
LANGUAGES: dict[str, str] = {
    "ru": "Русский",
    "en": "English",
    "uz": "O‘zbekcha",
    "kk": "Қазақша",
}

UI_STRINGS: dict[str, dict[str, str]] = {
    "ru": {
        "updated": "Дата вступления в силу: 15 октября 2026 г.",
        "contact": "Связаться с нами",
        "privacy": "Политика конфиденциальности",
        "terms": "Пользовательское соглашение",
        "delete-account": "Удаление аккаунта",
        "language": "Язык",
    },
    "en": {
        "updated": "Effective date: October 15, 2026",
        "contact": "Contact us",
        "privacy": "Privacy Policy",
        "terms": "Terms of Service",
        "delete-account": "Delete account",
        "language": "Language",
    },
    "uz": {
        "updated": "Kuchga kirish sanasi: 2026-yil 15-oktabr",
        "contact": "Biz bilan bog‘lanish",
        "privacy": "Maxfiylik siyosati",
        "terms": "Foydalanish shartlari",
        "delete-account": "Hisobni o‘chirish",
        "language": "Til",
    },
    "kk": {
        "updated": "Күшіне енген күні: 2026 жылғы 15 қазан",
        "contact": "Бізбен байланысу",
        "privacy": "Құпиялылық саясаты",
        "terms": "Пайдалану шарттары",
        "delete-account": "Аккаунтты жою",
        "language": "Тіл",
    },
}
