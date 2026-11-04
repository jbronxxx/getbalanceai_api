"""Профили нагрузки для Locust: ступени, длительная нагрузка, всплески и стресс до отказа.

Профиль задаётся числом виртуальных пользователей. MobileUser ждёт между действиями 5–15 с,
поэтому один пользователь даёт около 0.1 RPS: 500 пользователей ≈ 50 RPS (целевая нагрузка).
"""

from locust import LoadTestShape

# (длительность ступени в секундах, число пользователей, скорость запуска пользователей в секунду)
Stage = tuple[int, int, float]


class StagesShape(LoadTestShape):
    """Последовательность ступеней; после последней тест завершается."""

    abstract = True
    stages: tuple[Stage, ...] = ()

    def tick(self) -> tuple[int, float] | None:
        """Вернуть число пользователей и скорость запуска для текущего момента теста."""
        run_time = self.get_run_time()
        elapsed = 0
        for duration, users, spawn_rate in self.stages:
            elapsed += duration
            if run_time < elapsed:
                return users, spawn_rate
        return None


class StepsShape(StagesShape):
    """Ступенчатый рост 10 → 25 → 50 RPS по 5 минут: видно, на какой ступени растёт латентность."""

    stages = ((300, 100, 10), (300, 250, 10), (300, 500, 10))


class SoakShape(StagesShape):
    """Целевые 50 RPS 40 минут: утечки памяти, рост свопа, деградация p95 со временем."""

    stages = ((2400, 500, 10),)


class SpikeShape(StagesShape):
    """Гидроудар: фон 5 RPS, за 5 секунд — 50 RPS на 3 минуты, затем возврат к фону для оценки восстановления."""

    stages = ((120, 50, 10), (180, 500, 100), (180, 50, 100))


class DoubleSpikeShape(StagesShape):
    """Повторный удар через минуту после первого: сервер должен восстановиться, а не деградировать лавинообразно."""

    stages = ((120, 50, 10), (120, 500, 100), (60, 50, 100), (120, 500, 100), (180, 50, 100))


class StressShape(StagesShape):
    """Рост до отказа: 50 → 100 → 150 → 200 → 300 RPS по 3 минуты, ищем ступень с ошибками и узкое место."""

    stages = ((180, 500, 20), (180, 1000, 20), (180, 1500, 20), (180, 2000, 20), (180, 3000, 20))


SHAPES: dict[str, type[StagesShape]] = {
    "steps": StepsShape,
    "soak": SoakShape,
    "spike": SpikeShape,
    "double_spike": DoubleSpikeShape,
    "stress": StressShape,
}
