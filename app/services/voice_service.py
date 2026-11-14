"""Разбор голосового ввода: распознанная на устройстве фраза → черновики записей через LLM.

Сервер ничего не сохраняет: черновики возвращаются приложению на подтверждение. Текст фразы и ответ модели
не пишутся в журналы — в них личные траты пользователя.
"""

import uuid
from datetime import date, datetime, timezone, tzinfo
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from sqlalchemy.ext.asyncio import AsyncSession

from app.exceptions import PlanLimitReachedException, ServiceUnavailableException
from app.models.models import (
    EXPENSE_CATEGORIES,
    INCOME_CATEGORIES,
    SUPPORTED_CURRENCIES,
    Category,
    CurrencyCode,
    TransactionType,
)
from app.schemas.schemas import (
    DESCRIPTION_MAX_LENGTH,
    MAX_MONEY_AMOUNT,
    VOICE_RECORDS_MAX,
    PlanFeature,
    VoiceParseResponse,
    VoiceRecordDraft,
)
from app.services.ai_usage import AIRequestKind
from app.services.insights_formatting import category_name
from app.services.llm import (
    LLMClient,
    LLMRequest,
    ai_configured,
    extract_json,
    get_llm_client,
    sanitize_prompt_text,
)
from app.services.plan_limits import release_plan_quota, take_plan_quota
from app.services.subscription_service import SubscriptionService
from app.services.voice_texts import voice_texts
from logger.logger import get_logger

logger = get_logger(__name__)

_CENT = Decimal("0.01")
# Порядок категорий в инструкции — как в приложении; «Прочее» — последней
_EXPENSE_ORDER = [c for c in Category if c in EXPENSE_CATEGORIES and c is not Category.other] + [Category.other]
_INCOME_ORDER = [c for c in Category if c in INCOME_CATEGORIES and c is not Category.other] + [Category.other]


class VoiceParseService:
    """Разбор фразы на записи через LLM с суточным лимитом по тарифу пользователя."""

    def __init__(self, db: AsyncSession, client: LLMClient | None = None):
        """Сессия запроса (только чтобы отпустить соединение до вызова LLM); клиент LLM подменяется в тестах."""
        self.db = db
        self.client = client or get_llm_client()

    async def parse(
        self,
        user_id: uuid.UUID,
        text: str,
        locale: str,
        currency: CurrencyCode,
        tz: tzinfo = timezone.utc,
    ) -> VoiceParseResponse:
        """Разобрать фразу на черновики записей.

        Исключения: `PlanLimitReachedException` (403) — суточный лимит тарифа исчерпан;
        `ServiceUnavailableException` (503) — ключи LLM не заданы, Redis или модель недоступны, ответ модели
        не разобран. Неудачный вызов модели квоту не расходует.
        """
        phrase = sanitize_prompt_text(text)
        if not ai_configured():
            logger.warning("Ключи LLM не заданы (ANTHROPIC_API_KEY, GEMINI_API_KEY): голосовой ввод недоступен")
            raise ServiceUnavailableException(message="Разбор голосового ввода недоступен")

        plan = await SubscriptionService(self.db).plan(user_id)
        try:
            ticket = await take_plan_quota(user_id, plan, PlanFeature.voice, tz)
        except PlanLimitReachedException:
            raise
        except Exception as err:
            logger.warning(f"Не удалось занять лимит голосового ввода в Redis для пользователя {user_id}: {err}")
            raise ServiceUnavailableException(message="Разбор голосового ввода недоступен") from err

        now = datetime.now(timezone.utc)
        today = now.astimezone(tz).date()
        # Пользователь прочитан при авторизации: завершаем читающую транзакцию, чтобы соединение вернулось
        # в пул и не простаивало весь вызов LLM (до AI_REQUEST_TIMEOUT секунд)
        await self.db.commit()
        try:
            raw_records = await self._call_model(phrase, locale, currency, today)
        except Exception as err:
            await release_plan_quota(ticket)
            logger.error(f"Ошибка разбора голосового ввода в LLM для пользователя {user_id}: {type(err).__name__}: {err}")
            raise ServiceUnavailableException(message="Разбор голосового ввода недоступен") from err

        records = self._normalize(raw_records, currency, today)
        logger.info(
            f"Голосовой ввод пользователя {user_id}: длина фразы {len(phrase)}, записей {len(records)} "
            f"из {len(raw_records)}, тариф {plan.value}, разборов за сутки {ticket.used}/{ticket.limit}"
        )
        return VoiceParseResponse(records=records)

    @classmethod
    def build_request(cls, phrase: str, locale: str, currency: CurrencyCode, today: date) -> tuple[str, str]:
        """Системная инструкция и промпт разбора; тот же запрос шлёт скрипт замера scripts/measure_ai_cost.py."""
        texts = voice_texts(locale)
        system_instruction = texts.system_instruction.format(
            today=today.isoformat(),
            weekday=texts.weekdays[today.weekday()],
            currency=currency.value,
            currencies=", ".join(SUPPORTED_CURRENCIES),
            expense_categories=cls._categories_hint(_EXPENSE_ORDER, locale),
            income_categories=cls._categories_hint(_INCOME_ORDER, locale),
            max_records=VOICE_RECORDS_MAX,
        )
        return system_instruction, texts.prompt.format(text=phrase)

    async def _call_model(self, phrase: str, locale: str, currency: CurrencyCode, today: date) -> list:
        """Запрос к LLM; возвращает сырой список записей из ответа."""
        system_instruction, prompt = self.build_request(phrase, locale, currency, today)
        # temperature=0 — только для запасной Gemini: разбор должен быть воспроизводимым
        text = await self.client.generate(LLMRequest(AIRequestKind.voice, system_instruction, prompt, temperature=0))
        try:
            records = extract_json(text).get("records", [])
        except (ValueError, AttributeError) as err:
            # Сырой ответ не логируется: в нём траты пользователя
            raise ValueError(f"Невалидный JSON в ответе LLM, длина ответа {len(text)}") from err
        if not isinstance(records, list):
            raise ValueError("В ответе LLM поле records — не список")
        return records

    @staticmethod
    def _categories_hint(categories: list[Category], locale: str) -> str:
        """Коды категорий с названиями на языке интерфейса: «food (Питание), transport (Транспорт)»."""
        return ", ".join(f"{c.value} ({category_name(c, locale)})" for c in categories)

    @classmethod
    def _normalize(cls, raw_records: list, currency: CurrencyCode, today: date) -> list[VoiceRecordDraft]:
        """Проверить записи модели: без суммы или типа — отбросить, остальное исправить до допустимого.

        Категория не к типу — «Прочее»; валюта вне списка — валюта запроса; дата в будущем или не разобрана —
        сегодня; описание — одной строкой и не длиннее колонки.
        """
        drafts: list[VoiceRecordDraft] = []
        for item in raw_records:
            if len(drafts) >= VOICE_RECORDS_MAX:
                break
            if not isinstance(item, dict):
                continue
            try:
                tx_type = TransactionType(str(item.get("type")))
            except ValueError:
                continue
            amount = cls._amount(item.get("amount"))
            if amount is None:
                continue
            drafts.append(
                VoiceRecordDraft(
                    type=tx_type,
                    category=cls._category(item.get("category"), tx_type),
                    amount=amount,
                    currency=cls._currency(item.get("currency"), currency),
                    description=sanitize_prompt_text(str(item.get("description") or ""))[:DESCRIPTION_MAX_LENGTH],
                    date=cls._date(item.get("date"), today),
                )
            )
        return drafts

    @staticmethod
    def _amount(value) -> Decimal | None:
        """Сумма с двумя знаками; не число, не больше нуля или больше предела — None."""
        if isinstance(value, bool) or not isinstance(value, (int, float, str)):
            return None
        try:
            amount = Decimal(str(value).replace(",", ".").replace(" ", "")).quantize(_CENT, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            return None
        if not amount.is_finite() or amount <= 0 or amount > MAX_MONEY_AMOUNT:
            return None
        return amount

    @staticmethod
    def _category(value, tx_type: TransactionType) -> Category:
        """Категория из ответа, если она подходит к типу; иначе «Прочее»."""
        try:
            category = Category(str(value))
        except ValueError:
            return Category.other
        allowed = INCOME_CATEGORIES if tx_type == TransactionType.income else EXPENSE_CATEGORIES
        return category if category in allowed else Category.other

    @staticmethod
    def _currency(value, default: CurrencyCode) -> CurrencyCode:
        """Поддерживаемая валюта из ответа; иначе валюта запроса."""
        try:
            return CurrencyCode(str(value).upper())
        except ValueError:
            return default

    @staticmethod
    def _date(value, today: date) -> date:
        """День из ответа (ГГГГ-ММ-ДД), не позже сегодняшнего; не разобран — сегодня."""
        try:
            day = date.fromisoformat(str(value))
        except ValueError:
            return today
        return min(day, today)
