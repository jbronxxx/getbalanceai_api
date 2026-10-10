# Finance App API

FastAPI бэкенд для персонального финансового трекера с аналитикой расходов и AI-рекомендациями на базе Anthropic Claude.

---

## 🛠 Технологический стек
* **Язык:** Python 3.12 (поддерживается 3.11+)
* **Веб-фреймворк:** FastAPI + Uvicorn
* **База данных:** PostgreSQL 16
* **ORM & Миграции:** SQLAlchemy 2.0 + Alembic
* **Безопасность & Авторизация:** JWT Access & Refresh токенов с ротацией (`python-jose`) + хеширование паролей (`bcrypt`)
* **AI Интеграция:** Anthropic Claude API (модель `claude-haiku-4-5`)
* **Контейнеризация:** Docker & Docker Compose
* **Тестирование:** Pytest + HTTPX / TestClient

> 📖 **Подробная документация для разработчиков** (схема БД, паттерны, структура проекта) находится в отдельном файле: [**ARCHITECTURE.md**](ARCHITECTURE.md).

---

## 📋 Требования к окружению

Для работы с проектом необходимо наличие одного из вариантов:

### Вариант 1 (Запуск через Docker — рекомендуется):
* **Docker Engine** (версия 24.0+)
* **Docker Compose** (версия 2.20+)

### Вариант 2 (Локальный запуск на хосте):
* **Python** 3.11 или 3.12
* **PostgreSQL** 15+ (локально или в Docker)
* **Git**

---

## 🚀 Быстрый запуск в Docker (Рекомендуемый способ)

Все сервисы (API и PostgreSQL) запускаются одной командой. Приложение автоматически ожидает готовности базы данных (`healthcheck`).

### 1. Подготовка конфигурации
Скопируйте файл переменных окружения:
```bash
cp .env.example .env
```

Заполните ваши параметры в файле `.env` (при необходимости измените пароли и логин БД):
```env
POSTGRES_USER=your_postgres_user
POSTGRES_PASSWORD=your_postgres_password
POSTGRES_DB=your_postgres_db
DATABASE_URL=postgresql://your_postgres_user:your_postgres_password@db:5432/your_postgres_db
SECRET_KEY=change-me-in-production-use-long-random-string
ACCESS_TOKEN_EXPIRE_MINUTES=60
REFRESH_TOKEN_EXPIRE_DAYS=7
GEMINI_API_KEY=sk-ant-api03-...
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=admin
```
> [!NOTE]
> Если ключ `GEMINI_API_KEY` не указан или оставлен пустым, сервис AI-инсайтов автоматически переключится на безопасный режим заглушек и приложение продолжит работать без ошибок.

### 2. Запуск контейнеров (Локальная разработка)
```bash
# Сборка и запуск в фоновом режиме (без Nginx)
docker compose up --build -d
```

### 3. Запуск в Production (на боевом сервере с Nginx и SSL)
Перед запуском в production убедитесь, что вы подготовили `.env` файл:
1. Обязательно установите `APP_DEBUG=false`.
2. Сгенерируйте криптографически стойкий секретный ключ командой `openssl rand -hex 32` и вставьте его в `SECRET_KEY`.

Для запуска на боевом сервере с Reverse Proxy (Nginx) и сертификатами (Certbot):
1. Отредактируйте файл `nginx/conf.d/default.conf` и убедитесь, что там указан ваш домен (например, `api.getbalanceai.app`).
2. Запустите Docker, склеив основной и production конфигурации:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

### 3.1. Настройка автоматического резервного копирования БД
Для регулярного бекапа базы данных добавлен скрипт `scripts/backup_db.sh`.
Рекомендуется настроить его выполнение через системный `cron`. Например, для ежедневного запуска в 3 часа ночи:
```bash
crontab -e
# Добавьте строку:
0 3 * * * /absolute/path/to/finance_app/scripts/backup_db.sh
```

### 4. Просмотр логов
```bash
# Логи всех сервисов
docker compose logs -f

# Логи только API
docker compose logs -f api
```

### 5. Остановка контейнеров
```bash
# Остановка сервисов
docker compose down

# Остановка с удалением данных базы (полный сброс)
docker compose down -v
```

---

## 💻 Локальный запуск без Docker (Разработка)

### Быстрый запуск с помощью скрипта автоматизации (Рекомендуется)

Для полной автоматической подготовки окружения предусмотрены готовые скрипты:
* **Windows (PowerShell):**
  ```powershell
  . .\scripts\setup_dev.ps1
  ```
  *(Символ точки перед путем сохраняет активацию виртуального окружения в текущей сессии консоли).*
  
  Если запуск скриптов ограничен политикой выполнения PowerShell:
  ```powershell
  powershell -ExecutionPolicy Bypass -File .\scripts\setup_dev.ps1
  ```

* **macOS / Linux (Bash / Zsh):**
  ```bash
  chmod +x ./scripts/setup_dev.sh
  source ./scripts/setup_dev.sh
  ```
  *(Команда `source` сохраняет активацию виртуального окружения в текущей оболочке).*

#### Что скрипт делает автоматически в правильном порядке:
1. Создает файл `.env` из `.env.example`, если он еще не существует.
2. Создает и активирует виртуальное окружение `.venv`.
3. Обновляет `pip` и устанавливает все зависимости из `requirements.txt` и `requirements-dev.txt` (с безопасным выбором готовых бинарных сборок).
4. Устанавливает git-хуки `pre-commit` (pre-commit и pre-push проверки качества кода).
5. Запускает контейнеры инфраструктуры (`PostgreSQL` и `Redis`) в Docker.
6. Дожидается полной готовности базы данных (`pg_isready`).
7. Накатывает миграции Alembic (`alembic upgrade head`).

После завершения работы скрипта запустите сервер разработки:
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

### Альтернатива: Ручной пошаговый запуск

Если вы хотите выполнить развертывание вручную:

#### 1. Запуск базы данных и Redis в Docker
```bash
docker compose up -d db redis
```

#### 2. Создание и активация виртуального окружения
```bash
python3 -m venv .venv
source .venv/bin/activate  # Для macOS/Linux
# .venv\Scripts\activate   # Для Windows
```

#### 3. Установка зависимостей и хуков
```bash
pip install --upgrade pip
pip install --prefer-binary -r requirements.txt
pip install -r requirements-dev.txt

# Установка pre-commit хуков
pre-commit install
pre-commit install --hook-type pre-push
```

#### 4. Применение миграций базы данных
```bash
# Для локального запуска укажите локальный хост 127.0.0.1 / localhost:
DATABASE_URL="postgresql://${POSTGRES_USER:-db_user}:${POSTGRES_PASSWORD:-db_pass}@127.0.0.1:5432/${POSTGRES_DB:-get_balance_db}" alembic upgrade head
```

#### 5. Запуск сервера разработки
```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

---

## 📖 Как пользоваться API

После запуска сервис доступен по адресам:
* **Интерактивная документация Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc документация:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **Проверка статуса сервиса:** [http://localhost:8000/health](http://localhost:8000/health)

### Основные API-эндпоинты аутентификации:
- `POST /api/v1/auth/register` — регистрация пользователя
- `POST /api/v1/auth/login` — аутентификация и получение пары `access_token` и `refresh_token`
- `POST /api/v1/auth/refresh` — бессрочное/бесшовное обновление `access_token` с помощью `refresh_token` (механизм Refresh Token Rotation)
- `POST /api/v1/auth/logout` — выход из системы и отзыв активного токена
- `GET /api/v1/auth/me` — получение данных текущего авторизованного пользователя

---

## 📊 Мониторинг и Логирование (Observability)

В проект встроена инфраструктура для сбора логов и метрик:
* **Grafana** (Дашборды и визуализация) — доступна по адресу `http://localhost:3000` (логин/пароль настраиваются через `GRAFANA_ADMIN_USER` и `GRAFANA_ADMIN_PASSWORD` в `.env`).
* **Loki + Promtail** — автоматический сбор всех логов контейнеров.
* **Prometheus + cAdvisor** — сбор системных метрик контейнеров (CPU, RAM) и метрик самого приложения FastAPI (`/metrics`).

Базовые дашборды для метрик и логов уже преднастроены и доступны в Grafana сразу после запуска контейнеров!

---

## 🗄 Подключение к базе данных (DBeaver / DataGrip / psql)

Параметры для подключения сторонних GUI-клиентов берутся из вашего локального файла `.env`:
* **Хост:** `localhost` (при обращении с локальной машины к проброшенному порту)
* **Порт:** значение `POSTGRES_PORT` из `.env` (по умолчанию `5432`)
* **Имя БД:** значение `POSTGRES_DB` из `.env`
* **Пользователь:** значение `POSTGRES_USER` из `.env`
* **Пароль:** значение `POSTGRES_PASSWORD` из `.env`

---

## 🧪 Тестирование и пайплайн качества

Для ручного запуска автоматических тестов:
```bash
# Активируйте виртуальное окружение
source .venv/bin/activate

# Запуск тестов
pytest tests/ -v

# Запуск тестов с подсчетом покрытия (Coverage)
pytest --cov=app --cov-report=term-missing tests/
```

Проект защищен от попадания нерабочего кода:
1. **Git pre-push hook:** Автоматически запускает тесты локально при `git push`.
2. **Docker Multi-stage build:** В `Dockerfile` есть этап тестирования. Контейнер не соберется, если тесты упадут.
3. Полный список тестовых проверок, как ручных, так и автоматизированных, находится в документе [**TEST_CHECKLIST.md**](TEST_CHECKLIST.md).

---

## 🧹 Регламентная очистка устаревших токенов

Приложение автоматически выполняет ежедневную фоновую очистку истекших и отозванных JWT-токенов старше 30 дней в жизненном цикле сервера FastAPI.

Также доступен автономный CLI-скрипт для ручного запуска или настройки через системный cron:
```bash
# Очистка токенов старше 30 дней (по умолчанию)
python scripts/cleanup_tokens.py

# Очистка токенов с кастомным сроком хранения (например, 14 дней)
python scripts/cleanup_tokens.py --days 14
```


---

## 🔄 Миграции базы данных (Alembic)

Проект использует **Alembic** для управления схемой БД. Все таблицы создаются и обновляются через миграции.

### При запуске в Docker:

```bash
# Создание новой миграции на основе изменений в моделях
docker-compose exec api alembic revision --autogenerate -m "description_of_changes"

# Применение всех миграций
docker-compose exec api alembic upgrade head

# Откат последней миграции
docker-compose exec api alembic downgrade -1

# Просмотр статуса миграций
docker-compose exec api alembic current
```

### При локальном запуске (с активированным venv):

```bash
# Создание новой миграции
alembic revision --autogenerate -m "description_of_changes"

# Применение миграций
alembic upgrade head

# Откат последней миграции
alembic downgrade -1
```

> [!TIP]
> Миграции хранятся в папке `migrations/versions/`. Каждая миграция содержит функции `upgrade()` и `downgrade()` для управления схемой БД.

---

# Откат последней миграции
alembic downgrade -1
```

> [!TIP]
> Миграции хранятся в папке `migrations/versions/`. Каждая миграция содержит функции `upgrade()` и `downgrade()` для управления схемой БД.
