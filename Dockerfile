# syntax=docker/dockerfile:1
# Тесты в образе не запускаются: их выполняет CI (main.yml), и деплой стартует только после его успеха
FROM python:3.12-slim AS production
WORKDIR /app

COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --default-timeout=100 -r requirements.txt
COPY . .

EXPOSE 8000
# Keep-alive дольше, чем клиент держит простаивающее соединение (Dart HttpClient — 15 с):
# иначе по умолчанию через 2 с сервер закрывает соединение в момент, когда клиент
# отправляет по нему запрос, и тот обрывается без ответа.
# X-Forwarded-For принимается от любого адреса: порт 8000 доступен только из сети Docker и с хоста (127.0.0.1),
# а nginx перезаписывает заголовок адресом клиента. Подсеть Docker указать нельзя — gunicorn не принимает CIDR
CMD ["gunicorn", "main:app", "-w", "4", "-k", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:8000", "--keep-alive", "75", "--forwarded-allow-ips", "*"]
