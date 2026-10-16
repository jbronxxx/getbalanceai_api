"""Названия категорий и формат сумм для данных AI-отчёта — так же, как их показывает приложение.

Модель повторяет названия и суммы из переданных данных, поэтому они должны совпадать с экраном:
«Питание», а не «food» или «Еда»; «154 771 сум», а не «154771.00 UZS».
"""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.models.models import Category, CurrencyCode

DEFAULT_LANG = "ru"

# Названия категорий из локализаций мобильного приложения (lib/l10n/app_*.arb, ключи category_*)
CATEGORY_NAMES: dict[str, dict[Category, str]] = {
    "ru": {
        Category.food: "Питание",
        Category.transport: "Транспорт",
        Category.entertainment: "Развлечения",
        Category.health: "Здоровье",
        Category.subscriptions: "Подписки",
        Category.shopping: "Покупки",
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
        Category.salary: "Salary",
        Category.freelance: "Freelance",
        Category.investments: "Investments",
        Category.transfers: "Transfers",
        Category.cashback: "Cashback",
        Category.sales: "Sales",
        Category.other: "Other",
    },
    "kk": {
        Category.food: "Тамақтану",
        Category.transport: "Көлік",
        Category.entertainment: "Ойын-сауық",
        Category.health: "Денсаулық",
        Category.subscriptions: "Жазылымдар",
        Category.shopping: "Сатып алулар",
        Category.salary: "Жалақы",
        Category.freelance: "Фриланс",
        Category.investments: "Инвестициялар",
        Category.transfers: "Аударымдар",
        Category.cashback: "Кэшбэк",
        Category.sales: "Сатулар",
        Category.other: "Басқа",
    },
    "uz": {
        Category.food: "Oziq-ovqat",
        Category.transport: "Transport",
        Category.entertainment: "O'yin-kulgi",
        Category.health: "Sog'liq",
        Category.subscriptions: "Obunalar",
        Category.shopping: "Xaridlar",
        Category.salary: "Maosh",
        Category.freelance: "Frilans",
        Category.investments: "Investitsiyalar",
        Category.transfers: "O'tkazmalar",
        Category.cashback: "Keshbek",
        Category.sales: "Sotuvlar",
        Category.other: "Boshqa",
    },
}

# Обозначение сума зависит от языка (ключ uzs_symbol в локализациях приложения)
UZS_SYMBOLS: dict[str, str] = {"ru": "сум", "en": "UZS", "kk": "сом", "uz": "so'm"}

# Обозначение валюты, ставится ли оно перед числом и число знаков после запятой (как Currency в приложении)
CURRENCY_FORMATS: dict[str, tuple[str, bool, int]] = {
    CurrencyCode.UZS.value: ("сум", False, 0),
    CurrencyCode.KZT.value: ("₸", False, 0),
    CurrencyCode.EUR.value: ("€", True, 2),
    CurrencyCode.USD.value: ("$", True, 2),
}


def lang_of(locale: str) -> str:
    """Язык из локали (`ru`, `ru_RU`, `uz-UZ`); неизвестный — русский."""
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

    Примеры (ru): «154 771 сум», «1 000 ₸», «$12,50».
    """
    lang = lang_of(locale)
    code = (currency or "").strip().upper()
    symbol, symbol_before, digits = CURRENCY_FORMATS.get(code, (code, False, 2))
    if code == CurrencyCode.UZS.value:
        symbol = UZS_SYMBOLS[lang]

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
