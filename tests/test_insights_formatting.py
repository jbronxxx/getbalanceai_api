"""Тесты формата категорий и сумм в данных для AI-отчёта (как в мобильном приложении)."""

from decimal import Decimal

from app.models.models import Category
from app.services.insights_formatting import category_name, format_money, lang_of


class TestInsightsFormatting:
    """Названия категорий и суммы совпадают с тем, что пользователь видит на экране."""

    def test_category_names_match_app_localization(self):
        """Категория — локализованное название приложения, а не код или синоним."""
        assert category_name(Category.food, "ru") == "Питание"
        assert category_name("food", "en") == "Food"
        assert category_name(Category.food, "kk") == "Тамақтану"
        assert category_name(Category.food, "uz_UZ") == "Oziq-ovqat"

    def test_unknown_category_and_locale_fallbacks(self):
        """Неизвестная локаль — русский; неизвестная категория — как есть."""
        assert lang_of("de") == "ru"
        assert category_name(Category.health, "de") == "Здоровье"
        assert category_name("unknown", "ru") == "unknown"

    def test_uzs_uses_local_symbol_and_space_groups(self):
        """Сум без копеек, разряды через пробел, обозначение по языку вместо кода."""
        assert format_money(Decimal("154771.40"), "UZS", "ru") == "154 771 сум"
        assert format_money(154771, "UZS", "uz") == "154 771 so'm"
        assert format_money(154771, "UZS", "kk") == "154 771 сом"
        assert format_money(154771, "UZS", "en") == "154,771 UZS"

    def test_symbol_position_and_decimals(self):
        """Тенге после числа без копеек; доллар и евро перед числом с двумя знаками."""
        assert format_money(Decimal("1000"), "KZT", "ru") == "1 000 ₸"
        assert format_money(Decimal("1234.5"), "USD", "ru") == "$1 234,50"
        assert format_money(Decimal("1234.5"), "EUR", "en") == "€1,234.50"
        assert format_money(Decimal("-50"), "USD", "en") == "-$50.00"

    def test_invalid_amount_does_not_raise(self):
        """Некорректная сумма не роняет формирование отчёта."""
        assert format_money("n/a", "USD", "ru") == "n/a $"
