"""Тексты AI-отчёта на языке пользователя: инструкция и запрос к модели, подписи данных, служебные сообщения.

Всё, что уходит в модель, — на языке отчёта: русская инструкция или русские подписи при другом языке
интерфейса тянули бы ответ на русский. Языки — как в приложении (ru, en); любой другой — английский
(`lang_of`).
"""

from dataclasses import dataclass

from app.services.insights_formatting import lang_of


@dataclass(frozen=True)
class InsightTexts:
    """Тексты отчёта на одном языке.

    Шаблоны заполняются через `str.format`: `limit_line` — {category}, {limit}, {spent}, {remaining},
    {status}; `equivalent` — {amount}, {base}; `system_instruction` — {money_example};
    `prompt` — {summary}, {currency}. Фигурные скобки JSON в шаблонах удвоены.
    """

    stub: str
    busy: tuple[str, ...]
    quota_exceeded: tuple[str, ...]
    limits_header: str
    no_limits: str
    limit_line: str
    over_limit: str
    within_limit: str
    transactions_header: str
    no_transactions: str
    income: str
    expense: str
    equivalent: str
    no_description: str
    system_instruction: str
    prompt: str


INSIGHT_TEXTS: dict[str, InsightTexts] = {
    "ru": InsightTexts(
        stub="В разработке...",
        busy=(
            "К сожалению, наши умные помощники сейчас очень заняты и не могут проанализировать данные.",
            "Пожалуйста, загляните за советами немного позже.",
        ),
        quota_exceeded=(
            "Вы достигли дневного лимита запросов к финансовому помощнику (максимум 5 обновлений в день).",
            "Пожалуйста, возвращайтесь завтра или продолжайте пользоваться текущей аналитикой.",
        ),
        limits_header="Блок 1: Сводка по лимитам категорий на месяц:",
        no_limits="Лимиты бюджетов на данный период не установлены.",
        limit_line="{category} | Лимит: {limit} | Потрачено: {spent} | Остаток: {remaining} | {status}",
        over_limit="Превышен",
        within_limit="В пределах лимита",
        transactions_header="Блок 2: Список транзакций:",
        no_transactions="Транзакции за данный период отсутствуют.",
        income="Доход",
        expense="Расход",
        equivalent="{amount} (эквивалент: {base})",
        no_description="Без описания",
        system_instruction=(
            "Ты — персональный финансовый ассистент. Анализируй расходы пользователя и соблюдение им "
            "установленных месячных лимитов бюджетов по категориям. Формируй практичные, понятные "
            "и действенные советы на основе предоставленных данных.\n"
            "Строгие правила:\n"
            "1. Отвечай только на русском языке.\n"
            "2. Используй ТОЛЬКО точные названия категорий, переданные в данных (например, пиши строго "
            "«Питание», никогда не заменяй синонимами вроде «Еда» и не переводи их).\n"
            "3. Записывай суммы точно в том формате, в котором они даны в данных: число с разделителями "
            "разрядов и обозначение валюты (например: {money_example}). Не заменяй обозначения валют "
            "банковскими кодами (USD, EUR), если в данных их нет.\n"
            "4. Не пересчитывай и не округляй суммы из данных: пользователь сверяет их с экраном приложения.\n"
            'Ответ всегда возвращай строго в формате JSON: {{"insights": ["совет 1", "совет 2", "совет 3"]}}.'
        ),
        prompt=(
            "Вот финансовые данные пользователя за выбранный период:\n\n"
            "{summary}\n\n"
            "Дай 3 конкретных совета по управлению финансами и бюджетом на основе этих данных.\n"
            "Учитывай как фактические транзакции, так и состояние месячных лимитов категорий (превышения, остатки).\n"
            "Выдача советов должна быть максимально простой, без сложных финансовых слов и фраз.\n"
            "Пиши доступным языком для среднестатистического человека.\n"
            "Базовая валюта пользователя — {currency}; суммы лимитов указаны в валюте каждого лимита.\n"
            "Соразмеряй советы с реалиями этих валют.\n"
            "Ответ — на русском языке. Названия категорий и суммы бери из данных без изменений.\n"
            'Формат ответа JSON: {{"insights": ["совет 1", "совет 2", "совет 3"]}}\n'
            "Только JSON, без лишнего текста."
        ),
    ),
    "en": InsightTexts(
        stub="In development...",
        busy=(
            "Unfortunately, our smart assistants are currently very busy and cannot analyze the data.",
            "Please check back for tips a little later.",
        ),
        quota_exceeded=(
            "You have reached your daily limit for financial assistant requests (maximum 5 updates per day).",
            "Please return tomorrow or continue using your current insights.",
        ),
        limits_header="Section 1: Monthly category limits:",
        no_limits="No budget limits are set for this period.",
        limit_line="{category} | Limit: {limit} | Spent: {spent} | Left: {remaining} | {status}",
        over_limit="Exceeded",
        within_limit="Within limit",
        transactions_header="Section 2: Transactions:",
        no_transactions="There are no transactions for this period.",
        income="Income",
        expense="Expense",
        equivalent="{amount} (equivalent: {base})",
        no_description="No description",
        system_instruction=(
            "You are a personal finance assistant. Analyze the user's spending and how well they stay "
            "within their monthly category budget limits. Give practical, clear and actionable tips "
            "based on the data provided.\n"
            "Strict rules:\n"
            "1. Respond only in English.\n"
            "2. Use ONLY the exact category names given in the data (for example, write exactly "
            '"Food"; never replace them with synonyms such as "Groceries" and never translate them).\n'
            "3. Write amounts exactly in the format given in the data: a number with digit grouping and "
            "a currency symbol (for example: {money_example}). Do not replace currency symbols with "
            "bank codes (USD, EUR) unless the data uses them.\n"
            "4. Do not convert or round amounts from the data: the user compares them with the app screen.\n"
            'Always return the answer strictly as JSON: {{"insights": ["tip 1", "tip 2", "tip 3"]}}.'
        ),
        prompt=(
            "Here is the user's financial data for the selected period:\n\n"
            "{summary}\n\n"
            "Give 3 specific tips on managing money and the budget based on this data.\n"
            "Take into account both the actual transactions and the state of the monthly category limits "
            "(overspending, what is left).\n"
            "Keep the tips as simple as possible, without complex financial terms.\n"
            "Write in plain language for an average person.\n"
            "The user's base currency is {currency}; limit amounts are in each limit's own currency.\n"
            "Keep the tips realistic for these currencies.\n"
            "Answer in English. Take category names and amounts from the data without changes.\n"
            'Response format JSON: {{"insights": ["tip 1", "tip 2", "tip 3"]}}\n'
            "JSON only, no extra text."
        ),
    ),
}


def insight_texts(locale: str) -> InsightTexts:
    """Тексты отчёта для локали (`ru`, `ru_RU`, `en-US`); неподдерживаемая — английские."""
    return INSIGHT_TEXTS[lang_of(locale)]
