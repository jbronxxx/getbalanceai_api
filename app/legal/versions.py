"""Версии юридических документов, которые пользователь принимает: соглашение и политика конфиденциальности.

Версия — дата вступления в силу в формате ISO (`2026-11-13`). Новая версия документа — новая запись в конце
истории со списком «Что изменилось» на каждом языке из `LANGUAGES`; тексты прежней версии при этом переносятся
в архив (`app/legal/archive.py`), чтобы по версии из записи о согласии можно было показать принятый текст.
Новая версия соглашения требует повторного принятия, новая версия политики — только уведомления.
"""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class LegalVersion:
    """Версия документа: дата вступления в силу и пункты «Что изменилось» по языкам."""

    effective: date
    changes: dict[str, list[str]]

    @property
    def version(self) -> str:
        """Строка версии — дата вступления в силу в ISO-формате."""
        return self.effective.isoformat()


# Документы, принятие которых записывается: slug страницы -> история версий, последняя — текущая
LEGAL_HISTORY: dict[str, tuple[LegalVersion, ...]] = {
    "terms": (
        LegalVersion(
            effective=date(2026, 11, 13),
            changes={
                "ru": [
                    "Условия нужно принять явно — отметкой на экране входа.",
                    "О новой версии Соглашения мы сообщаем в приложении; чтобы пользоваться аккаунтом дальше, "
                    "её нужно принять.",
                ],
                "en": [
                    "The Terms must be accepted explicitly with a checkbox on the sign-in screen.",
                    "We announce new versions of the Terms in the app; to keep using your account, "
                    "you need to accept them.",
                ],
            },
        ),
        LegalVersion(
            effective=date(2026, 11, 15),
            changes={
                "ru": [
                    "Новый раздел «Тарифы и подписка»: бесплатный тариф и Pro, лимиты функций, пробный период, "
                    "автопродление, отмена и возвраты.",
                    "В ЕС от подписки можно отказаться в течение 14 дней со дня покупки.",
                ],
                "en": [
                    "New section “Plans and subscription”: the free plan and Pro, feature limits, free trial, "
                    "auto-renewal, cancellation and refunds.",
                    "In the EU you may withdraw from a subscription within 14 days of purchase.",
                ],
            },
        ),
    ),
    "privacy": (
        LegalVersion(
            effective=date(2026, 11, 13),
            changes={
                "ru": [
                    "Мы храним дату и версию принятых вами документов.",
                    "После удаления аккаунта эта запись хранится 3 года, без имени и email.",
                    "Указали Cloudflare R2 — хранилище зашифрованных резервных копий.",
                ],
                "en": [
                    "We keep the date and version of the documents you accepted.",
                    "After account deletion this record is kept for 3 years, without your name or email.",
                    "We now list Cloudflare R2, where encrypted backups are stored.",
                ],
            },
        ),
        LegalVersion(
            effective=date(2026, 11, 14),
            changes={
                "ru": [
                    "ИИ-аналитику и разбор голосового ввода теперь выполняет Claude (Anthropic); Google Gemini — "
                    "запасной вариант, если Claude недоступен.",
                    "Состав передаваемых данных не изменился: без имени, email и описаний операций.",
                ],
                "en": [
                    "AI insights and voice input are now handled by Claude (Anthropic); Google Gemini is the backup "
                    "when Claude is unavailable.",
                    "The data we send has not changed: no name, email or transaction descriptions.",
                ],
            },
        ),
        LegalVersion(
            effective=date(2026, 11, 15),
            changes={
                "ru": [
                    "Подписки учитывает RevenueCat: он получает идентификатор аккаунта (без имени и email) "
                    "и данные покупок.",
                    "При удалении аккаунта данные в RevenueCat тоже удаляются.",
                ],
                "en": [
                    "Subscriptions are managed by RevenueCat: it receives your account identifier (without your "
                    "name or email) and purchase data.",
                    "When you delete your account, your data in RevenueCat is deleted too.",
                ],
            },
        ),
    ),
}

# Документы с записью о согласии (порядок — для ответов API)
LEGAL_DOCUMENTS: tuple[str, ...] = tuple(LEGAL_HISTORY)


def current_version(document: str) -> LegalVersion:
    """Текущая (последняя) версия документа."""
    return LEGAL_HISTORY[document][-1]
