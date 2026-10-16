# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base
WORKDIR /app

# Stage 1: Зависимости и тесты
FROM base AS test
COPY requirements.txt requirements-dev.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=100 -r requirements-dev.txt
COPY . .
# Запуск тестов. Если они упадут, сборка Docker завершится с ошибкой
RUN pytest tests/

# Stage 2: Продакшен образ (собирается только если тесты прошли)
FROM base AS production
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=100 -r requirements.txt
COPY . .

EXPOSE 8000
# Keep-alive дольше, чем клиент держит простаивающее соединение (Dart HttpClient — 15 с):
# иначе по умолчанию через 2 с сервер закрывает соединение в момент, когда клиент
# отправляет по нему запрос, и тот обрывается без ответа.
CMD ["gunicorn", "main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--keep-alive", "75"]
