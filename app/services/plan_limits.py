"""Лимиты функций по тарифу: ИИ-отчёт и голосовой ввод.

Free — `FREE_AI_REPORTS_PER_MONTH` отчётов в календарный месяц и `FREE_VOICE_PER_DAY` разборов в сутки;
Pro — `PRO_AI_REPORTS_PER_DAY` и `PRO_VOICE_PER_DAY` в сутки. Сутки и месяц — по часам пользователя
(заголовок X-Timezone-Offset). Счётчики — в Redis: `plan_usage:{функция}:{user_id}:{окно}`, окно — дата
`2026-11-14` или месяц `2026-11`, поэтому суточный и месячный счётчики одной функции не пересекаются.
Сменой часового пояса можно получить соседнее окно раньше времени — это не больше одного лишнего окна,
а вызов ИИ стоит доли цента.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone, tzinfo

from prometheus_client import Counter

from app.exceptions import PlanLimitReachedException
from app.redis import get_redis
from app.schemas.schemas import FeatureLimit, LimitPeriod, PlanCode, PlanFeature
from config_reader.config_reader import config
from logger.logger import get_logger

logger = get_logger(__name__)

PLAN_LIMIT_REACHED = Counter("plan_limit_reached", "Упоры в лимит функции на тарифе", ["feature", "plan"])

# Запас срока жизни счётчика после конца окна: соседние часовые пояса заканчивают окно позже
_COUNTER_TTL_MARGIN = timedelta(days=1)


@dataclass(frozen=True)
class PlanLimit:
    """Лимит функции: сколько раз за окно."""

    limit: int
    period: LimitPeriod


def plan_limit(plan: PlanCode, feature: PlanFeature) -> PlanLimit:
    """Лимит функции на тарифе по настройкам."""
    if plan is PlanCode.pro:
        if feature is PlanFeature.ai_report:
            return PlanLimit(config.pro_ai_reports_per_day, LimitPeriod.day)
        return PlanLimit(config.pro_voice_per_day, LimitPeriod.day)
    if feature is PlanFeature.ai_report:
        return PlanLimit(config.free_ai_reports_per_month, LimitPeriod.month)
    return PlanLimit(config.free_voice_per_day, LimitPeriod.day)


def usage_window(period: LimitPeriod, tz: tzinfo, now: datetime | None = None) -> tuple[str, datetime]:
    """Метка текущего окна (`2026-11-14` или `2026-11`) и начало следующего окна в UTC."""
    local = (now or datetime.now(timezone.utc)).astimezone(tz)
    if period is LimitPeriod.day:
        start = local.replace(hour=0, minute=0, second=0, microsecond=0)
        # Пояс клиента — фиксированное смещение без перехода на летнее время: сутки всегда 24 ч
        return start.strftime("%Y-%m-%d"), (start + timedelta(days=1)).astimezone(timezone.utc)
    year, month = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    next_start = datetime(year, month, 1, tzinfo=local.tzinfo)
    return local.strftime("%Y-%m"), next_start.astimezone(timezone.utc)


def plan_usage_key(feature: PlanFeature, user_id: uuid.UUID, window: str) -> str:
    """Ключ счётчика функции пользователя в окне."""
    return f"plan_usage:{feature.value}:{user_id}:{window}"


@dataclass(frozen=True)
class QuotaTicket:
    """Занятый вызов функции: номер вызова в окне и ключ счётчика, чтобы вернуть вызов при сбое."""

    key: str
    used: int
    limit: int


async def take_plan_quota(user_id: uuid.UUID, plan: PlanCode, feature: PlanFeature, tz: tzinfo) -> QuotaTicket:
    """Занять один вызов функции до обращения к ИИ.

    Атомарный INCR: параллельные запросы не проходят лимит вместе. Сверх лимита вызов сразу возвращается
    (счётчик не растёт выше лимита — после перехода на Pro остаток считается верно) и бросается
    `PlanLimitReachedException`. Ошибка Redis пробрасывается: без счётчика лимит не проверить, а вызов ИИ платный.
    """
    rule = plan_limit(plan, feature)
    now = datetime.now(timezone.utc)
    window, resets_at = usage_window(rule.period, tz, now)
    key = plan_usage_key(feature, user_id, window)
    redis = get_redis()
    used = await redis.incr(key)
    if used == 1:
        await redis.expire(key, int((resets_at - now + _COUNTER_TTL_MARGIN).total_seconds()))
    if used > rule.limit:
        await release_plan_quota(QuotaTicket(key=key, used=used, limit=rule.limit))
        PLAN_LIMIT_REACHED.labels(feature=feature.value, plan=plan.value).inc()
        logger.info(f"Лимит {feature.value} на тарифе {plan.value} исчерпан для пользователя {user_id} ({rule.limit})")
        raise PlanLimitReachedException(
            details={
                "feature": feature.value,
                "plan": plan.value,
                "limit": rule.limit,
                "period": rule.period.value,
                # Тот же вид, что у resets_at в профиле: UTC с суффиксом Z
                "resets_at": resets_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        )
    return QuotaTicket(key=key, used=used, limit=rule.limit)


async def release_plan_quota(ticket: QuotaTicket) -> None:
    """Вернуть занятый вызов: неудачный вызов ИИ и отказ по лимиту лимит не расходуют."""
    try:
        await get_redis().decr(ticket.key)
    except Exception as err:
        logger.warning(f"Не удалось вернуть вызов лимита в Redis ({ticket.key}): {err}")


async def plan_usage(user_id: uuid.UUID, plan: PlanCode, tz: tzinfo) -> list[FeatureLimit]:
    """Лимиты функций тарифа с расходом в текущих окнах; Redis недоступен — расход `None`."""
    now = datetime.now(timezone.utc)
    rows: list[tuple[PlanFeature, PlanLimit, str, datetime]] = []
    for feature in PlanFeature:
        rule = plan_limit(plan, feature)
        window, resets_at = usage_window(rule.period, tz, now)
        rows.append((feature, rule, plan_usage_key(feature, user_id, window), resets_at))

    counters: list[str | None] | None
    try:
        counters = await get_redis().mget([key for _, _, key, _ in rows])
    except Exception as err:
        logger.warning(f"Не удалось прочитать счётчики лимитов из Redis для пользователя {user_id}: {err}")
        counters = None

    limits: list[FeatureLimit] = []
    for index, (feature, rule, _, resets_at) in enumerate(rows):
        used = None if counters is None else min(max(int(counters[index] or 0), 0), rule.limit)
        limits.append(
            FeatureLimit(
                feature=feature,
                period=rule.period,
                limit=rule.limit,
                used=used,
                remaining=None if used is None else rule.limit - used,
                resets_at=resets_at,
            )
        )
    return limits
