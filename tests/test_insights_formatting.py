"""Тесты формата категорий и сумм в данных для AI-отчёта (как в мобильном приложении)."""

from decimal import Decimal

from app.models.models import Category
from app.services.insights_formatting import (
    CATEGORY_NAMES,
    category_name,
    format_money,
    lang_of,
)
from app.services.insights_texts import INSIGHT_TEXTS, insight_texts


class TestInsightsFormatting:
    """Названия категорий и суммы совпадают с тем, что пользователь видит на экране."""

    def test_category_names_match_app_localization(self):
        """Категория — локализованное название приложения, а не код или синоним."""
        assert category_name(Category.food, "ru") == "Питание"
        assert category_name("food", "en") == "Food"
        assert category_name(Category.food, "en_US") == "Food"

    def test_every_category_has_name_in_every_language(self):
        """Новая категория без подписи попала бы в отчёт кодом («housing») — у каждой есть название на всех языках."""
        for lang, names in CATEGORY_NAMES.items():
            assert set(names) == set(Category), lang
        assert category_name(Category.housing, "ru") == "Жильё и ЖКХ"
        assert category_name("communication", "en") == "Phone & internet"

    def test_unknown_category_and_locale_fallbacks(self):
        """Неизвестная локаль — английский; неизвестная категория — как есть."""
        assert lang_of("de") == "en"
        assert category_name(Category.health, "de") == "Health"
        assert category_name("unknown", "ru") == "unknown"

    def test_symbol_position_and_decimals(self):
        """Доллар и евро перед числом с двумя знаками, разряды — пробел в ru и запятая в en."""
        assert format_money(Decimal("154771.4"), "EUR", "ru") == "€154 771,40"
        assert format_money(Decimal("1234.5"), "USD", "ru") == "$1 234,50"
        assert format_money(Decimal("1234.5"), "EUR", "en") == "€1,234.50"
        assert format_money(Decimal("-50"), "USD", "en") == "-$50.00"

    def test_report_texts_exist_for_every_language(self):
        """Тексты отчёта есть для каждого языка категорий, шаблоны заполняются без ошибок."""
        assert set(INSIGHT_TEXTS) == set(CATEGORY_NAMES)
        for lang, texts in INSIGHT_TEXTS.items():
            assert texts.system_instruction.format(money_example="$1"), lang
            assert texts.prompt.format(summary="s", currency="USD"), lang
            assert texts.limit_line.format(category="c", limit="1", spent="2", remaining="3", status="s"), lang
            assert texts.equivalent.format(amount="1", base="2"), lang
        assert insight_texts("de") is INSIGHT_TEXTS["en"]
        assert insight_texts("ru_RU") is INSIGHT_TEXTS["ru"]

    def test_invalid_amount_does_not_raise(self):
        """Некорректная сумма не роняет формирование отчёта."""
        assert format_money("n/a", "USD", "ru") == "n/a $"
