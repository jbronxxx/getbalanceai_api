"""Названия категорий и формат сумм для данных AI-отчёта — так же, как их показывает приложение.

Модель повторяет названия и суммы из переданных данных, поэтому они должны совпадать с экраном:
«Питание», а не «food» или «Еда»; «$1 234,50», а не «1234.50 USD».
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.models.models import Category, CurrencyCode

DEFAULT_LANG = "en"

# Названия категорий из локализаций мобильного приложения (lib/l10n/app_*.arb, ключи category_*)
CATEGORY_NAMES: dict[str, dict[Category, str]] = {
    "ru": {
        Category.food: "Питание",
        Category.transport: "Транспорт",
        Category.entertainment: "Развлечения",
        Category.health: "Здоровье",
        Category.subscriptions: "Подписки",
        Category.shopping: "Покупки",
        Category.housing: "Жильё и ЖКХ",
        Category.communication: "Связь",
        Category.education: "Образование",
        Category.family: "Семья",
        Category.car: "Авто",
        Category.gifts: "Подарки",
        Category.travel: "Путешествия",
        Category.beauty: "Красота",
        Category.salary: "Зарплата",
        Category.freelance: "Фриланс",
        Category.investments: "Инвестиции",
        Category.transfers: "Переводы",
        Category.cashback: "Кэшбэк",
        Category.sales: "Продажи",
        Category.other: "Другое",
    },
    "en": {
        Category.food: "Food",
        Category.transport: "Transport",
        Category.entertainment: "Entertainment",
        Category.health: "Health",
        Category.subscriptions: "Subscriptions",
        Category.shopping: "Shopping",
        Category.housing: "Housing",
        Category.communication: "Phone & internet",
        Category.education: "Education",
        Category.family: "Family",
        Category.car: "Car",
        Category.gifts: "Gifts",
        Category.travel: "Travel",
        Category.beauty: "Beauty",
        Category.salary: "Salary",
        Category.freelance: "Freelance",
        Category.investments: "Investments",
        Category.transfers: "Transfers",
        Category.cashback: "Cashback",
        Category.sales: "Sales",
        Category.other: "Other",
    },
}

# Обозначение валюты, ставится ли оно перед числом и число знаков после запятой (как Currency в приложении)
CURRENCY_FORMATS: dict[str, tuple[str, bool, int]] = {
    CurrencyCode.EUR.value: ("€", True, 2),
    CurrencyCode.USD.value: ("$", True, 2),
}


def lang_of(locale: str) -> str:
    """Язык из локали (`ru`, `ru_RU`, `en-US`); неизвестный — английский."""
    lang = (locale or "")[:2].lower()
    return lang if lang in CATEGORY_NAMES else DEFAULT_LANG


def category_name(category: Category | str, locale: str) -> str:
    """Название категории, как в приложении на языке отчёта."""
    try:
        cat = category if isinstance(category, Category) else Category(str(category))
    except ValueError:
        return str(category)
    return CATEGORY_NAMES[lang_of(locale)].get(cat, cat.value)


def format_money(amount: Decimal | float | int, currency: str, locale: str) -> str:
    """Сумма как в приложении: разряды через пробел (в en — запятая), обозначение валюты вместо кода.

    Примеры: ru — «$1 234,50», «€12,50»; en — «$1,234.50».
    """
    lang = lang_of(locale)
    code = (currency or "").strip().upper()
    symbol, symbol_before, digits = CURRENCY_FORMATS.get(code, (code, False, 2))

    quantum = Decimal(1).scaleb(-digits)
    try:
        value = Decimal(str(amount)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError):
        return f"{amount} {symbol}"
    sign = "-" if value < 0 else ""
    integer, _, fraction = f"{abs(value):.{digits}f}".partition(".")

    group_sep, decimal_sep = (",", ".") if lang == "en" else (" ", ",")
    groups = []
    while len(integer) > 3:
        groups.insert(0, integer[-3:])
        integer = integer[:-3]
    groups.insert(0, integer)
    number = group_sep.join(groups) + (decimal_sep + fraction if fraction else "")

    return f"{sign}{symbol}{number}" if symbol_before else f"{sign}{number} {symbol}"
