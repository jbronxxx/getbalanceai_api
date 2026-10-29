# Finance App API

FastAPI бэкенд для персонального финансового трекера с аналитикой расходов и AI-рекомендациями на базе Google Gemini.

---

## 🛠 Технологический стек
* **Язык:** Python 3.12 (поддерживается 3.11+)
* **Веб-фреймворк:** FastAPI + Uvicorn
* **База данных:** PostgreSQL 16
* **ORM & Миграции:** SQLAlchemy 2.0 + Alembic
* **Безопасность & Авторизация:** JWT Access & Refresh токенов с ротацией (`python-jose`) + хеширование паролей (`bcrypt`)
* **AI Интеграция:** Google Gemini API (модель `gemini-3.5-flash-lite`)
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
1. Установите `APP_DEBUG=false` (значение по умолчанию) и `LOG_LEVEL=INFO`. Для локальной разработки в `.env` нужен `APP_DEBUG=true`.
2. Сгенерируйте криптографически стойкий секретный ключ командой `openssl rand -hex 32` и вставьте его в `SECRET_KEY`.
3. Задайте `GOOGLE_CLIENT_ID` — client ID через запятую, который приходит в `aud` токена (обычно Web client ID). Без него приложение в production не стартует. `APPLE_CLIENT_ID` (Bundle ID, Services ID) необязателен: пока он пуст, вход через Apple отклоняется.
4. Если нужен CORS с `CORS_ALLOW_CREDENTIALS=true`, перечислите домены в `CORS_ORIGINS`: сочетание с `*` в production запрещено.

Для запуска на боевом сервере с Reverse Proxy (Nginx) и сертификатами (Certbot):
1. Отредактируйте файл `nginx/conf.d/default.conf` и убедитесь, что там указан ваш домен (например, `api.getbalanceai.app`).
2. Запустите Docker, склеив основной и production конфигурации:
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build -d
```

Автодеплой (`.github/workflows/deploy.yml`) запускается после успешного завершения workflow `CI` на ветке `main` (или вручную через `workflow_dispatch`): подтягивает код, снимает бэкап БД (`scripts/backup_db.sh predeploy`; при ошибке деплой останавливается), собирает образ `api`, применяет миграции новым образом, принудительно пересоздаёт контейнер `api`, поднимает остальные сервисы и перезагружает конфигурацию Nginx (`nginx -t` + `nginx -s reload`). Скрипт деплоя передаётся на сервер через stdin, поэтому команды `docker compose run`/`exec` в нём запускаются с `</dev/null`: иначе они забирают остаток скрипта, и деплой молча завершается успехом, не дойдя до перезапуска.

### 3.1. Настройка автоматического резервного копирования БД
Для регулярного бекапа базы данных добавлен скрипт `scripts/backup_db.sh`.
Рекомендуется настроить его выполнение через системный `cron`. Например, для ежедневного запуска в 3 часа ночи:
```bash
crontab -e
# Добавьте строку:
0 3 * * * /absolute/path/to/finance_app/scripts/backup_db.sh
```

Хранение: в режиме по умолчанию (`daily`) скрипт оставляет 7 последних копий `db_backup_*.sql` (при ежедневном cron — около 7 суток). Деплой вызывает скрипт в режиме `predeploy`: копии `predeploy_backup_*.sql` хранятся отдельно, не более 3 штук и не дольше 7 суток, и не вытесняют ежедневные. Срок 7 суток указан в Политике конфиденциальности. Файлы создаются с правами только для владельца (`umask 077`). Логи Loki хранятся 30 дней (`loki/local-config.yaml`). После изменения конфига Loki на сервере выполните `docker compose restart loki`: `up -d` не перечитывает смонтированный файл.

Откат неудачного деплоя с миграциями выполняется восстановлением `predeploy`-копии, а не `alembic downgrade`: часть миграций при откате необратимо теряет данные (например, `h00700700700`, `i00800800800`).

### 3.2. Копия бэкапов вне сервера (Cloudflare R2)
Локальные копии пропадут вместе с сервером, поэтому ночной cron дополнительно выгружает свежие дампы в бакет Cloudflare R2 через `rclone`. Файлы шифруются на сервере (`rclone crypt`): дампы содержат персональные данные.

1. Cloudflare → R2: бакет `getbalanceai-backups`; в **Settings → Object lifecycle rules** — удаление объектов старше 7 дней (срок из Политики конфиденциальности).
2. R2 → Manage R2 API Tokens → **Account API token**: Object Read & Write, только бакет `getbalanceai-backups`, TTL Forever.
3. На сервере — rclone с официального сайта (версия из apt старая и отвечает R2 ошибкой `501 NotImplemented`):
   ```bash
   curl -fsSLO https://downloads.rclone.org/rclone-current-linux-amd64.deb && dpkg -i rclone-current-linux-amd64.deb && rm rclone-current-linux-amd64.deb
   ```
4. Remote-ы `r2` (доступ к бакету) и `r2crypt` (шифрование поверх него). Пробел в начале строки исключает команду из истории bash, `> /dev/null` — вывод конфига с ключами на экран:
   ```bash
    rclone config create r2 s3 provider=Cloudflare access_key_id=<KEY_ID> secret_access_key=<SECRET> endpoint=https://<ACCOUNT_ID>.r2.cloudflarestorage.com region=auto acl=private no_check_bucket=true > /dev/null
    rclone config create r2crypt crypt remote=r2:getbalanceai-backups filename_encryption=off directory_name_encryption=false password=<ПАРОЛЬ_ШИФРОВАНИЯ> --obscure > /dev/null
   chmod 600 /root/.config/rclone/rclone.conf
   ```
   Пароль шифрования (`openssl rand -base64 32`) хранится вне сервера, в менеджере паролей: без него копии в R2 не расшифровать. Команды `rclone config show/create/update` печатают секреты — их вывод не публикуется.
5. Строка cron (заменяет строку из п. 3.1). `copy`, а не `sync`: старые объекты удаляет lifecycle-правило бакета; `--max-age 1d` не даёт повторно выгрузить локальные копии, уже удалённые из R2:
   ```
   0 3 * * * /opt/getbalanceai_api/scripts/backup_db.sh >> /var/log/getbalanceai_backup.log 2>&1 && rclone copy /opt/getbalanceai_api/backups r2crypt: --max-age 1d >> /var/log/getbalanceai_backup.log 2>&1
   ```
6. Проверка: `rclone ls r2crypt:` (имена файлов), `rclone cryptcheck backups/ r2crypt: --max-age 1d` (`0 differences found`).

### 3.3. Восстановление из бэкапа
Дамп — plain SQL без `--clean`, поэтому накатывается только в пустую БД. Регулярная проверка выполняется в отдельную БД `restore_test` рядом с боевой:
```bash
cd /opt/getbalanceai_api
alias dc='docker compose -f docker-compose.yml -f docker-compose.prod.yml'

# Локальная копия или скачанная из R2
LAST=$(ls -t backups/db_backup_*.sql | head -1)
# F=$(rclone lsf r2crypt: | sort | tail -1); mkdir -p /root/r2_restore && rclone copy "r2crypt:$F" /root/r2_restore/ && LAST="/root/r2_restore/$F"

dc exec -T db sh -c 'createdb -U "$POSTGRES_USER" restore_test'
dc exec -T db sh -c 'psql -U "$POSTGRES_USER" -d restore_test -v ON_ERROR_STOP=1 -q' < "$LAST" && echo RESTORE_OK

# Сравнение с боевой БД: количество строк и версия миграций
for DB in '$POSTGRES_DB' restore_test; do
  echo "== $DB"
  dc exec -T db sh -c "psql -U \"\$POSTGRES_USER\" -d $DB -tAc \"
    SELECT 'users', count(*)::text FROM users UNION ALL
    SELECT 'transactions', count(*)::text FROM transactions UNION ALL
    SELECT 'alembic', version_num FROM alembic_version;\""
done

dc exec -T db sh -c 'dropdb -U "$POSTGRES_USER" restore_test'
```

Восстановление боевой БД (только при аварии): `dc stop api` → `./scripts/backup_db.sh predeploy` (копия текущего состояния) → `dropdb` и `createdb` для `$POSTGRES_DB` → накат дампа командой выше в `$POSTGRES_DB` → `dc start api`.

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
uvicorn main:app --reload --host 0.0.0.0 --port 8000 --timeout-keep-alive 75
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
uvicorn main:app --reload --host 0.0.0.0 --port 8000 --timeout-keep-alive 75
```

`--timeout-keep-alive 75` обязателен при работе с мобильным клиентом: по умолчанию uvicorn закрывает простаивающее
соединение через 5 с, а клиент переиспользует его до 15 с — запрос по закрытому соединению обрывается с ошибкой
«Connection closed before full header was received». В Docker то же задаёт `--keep-alive 75` у gunicorn (`Dockerfile`).

---

## 📖 Как пользоваться API

После запуска сервис доступен по адресам:
* **Интерактивная документация Swagger UI:** [http://localhost:8000/docs](http://localhost:8000/docs)
* **ReDoc документация:** [http://localhost:8000/redoc](http://localhost:8000/redoc)
* **Проверка статуса сервиса:** [http://localhost:8000/health](http://localhost:8000/health)

На проде `/docs`, `/redoc` и `/openapi.json` открываются только по учётке basic auth (см. «Доступ к служебным ресурсам»), `/metrics` снаружи закрыт; сам API (`/api/v1/...`) работает без изменений.

### Основные API-эндпоинты:
- **Аутентификация (`/api/v1/auth`):**
  - `POST /api/v1/auth/register` — регистрация пользователя
  - `POST /api/v1/auth/login` — аутентификация и получение пары `access_token` и `refresh_token`
  - `POST /api/v1/auth/refresh` — бессрочное/бесшовное обновление `access_token` с помощью `refresh_token` (механизм Refresh Token Rotation)
  - `POST /api/v1/auth/logout` — выход из системы и отзыв активного токена
  - `GET /api/v1/auth/me` — получение данных текущего авторизованного пользователя
  - `PATCH /api/v1/auth/me` — обновление профиля (имя, аватар, базовая валюта `base_currency`)
- **Публичные страницы (без авторизации):**
  - `GET /privacy` — политика конфиденциальности (ru/en/uz/kk)
  - `GET /terms` — пользовательское соглашение (ru/en/uz/kk)
  - `GET /delete-account` — порядок удаления аккаунта и данных; URL для Data Safety в Google Play
  - Язык выбирается параметром `?lang=ru|en|uz|kk`, иначе по `Accept-Language` (по умолчанию `en`), например `https://<домен>/privacy?lang=kk`
- **Валютные котировки (`/api/v1/currencies`):**
  - `GET /api/v1/currencies/rates` — актуальный справочник курсов валют (кэширование в Redis)
- **Операции и Бюджеты:**
  - `POST /api/v1/transactions/` — добавление транзакции с поддержкой мультивалютности (`currency`, `exchange_rate`, `base_amount`)
  - `GET /api/v1/transactions/` — список транзакций с курсорной пагинацией и ETag
  - `GET /api/v1/budgets/` — лимиты бюджетов и подсчет расходов в базовой валюте
  - `POST /api/v1/sync/` — пакетная офлайн-синхронизация данных
  - `GET /api/v1/insights/` — финансовая AI-аналитика расходов

---

## 📊 Мониторинг и Логирование (Observability)

В проект встроена инфраструктура для сбора логов и метрик:
* **Grafana** (Дашборды и визуализация) — локально `http://localhost:3000`, на проде `https://grafana.getbalanceai.app` (порт 3000 опубликован только на `127.0.0.1`, снаружи доступ лишь через nginx). Анонимный доступ и саморегистрация отключены. Логин/пароль администратора — `GRAFANA_ADMIN_USER` и `GRAFANA_ADMIN_PASSWORD` в `.env` (пароль обязателен); они применяются только при первом создании тома `grafana_data`, позже пароль меняется командой `docker compose exec grafana grafana-cli admin reset-admin-password <пароль>`.
  * Учётки для команды создаёт администратор: **Administration → Users → New user** (роль по умолчанию — Viewer). Viewer видит только дашборды, без Explore (`GF_USERS_VIEWERS_CAN_EDIT=false`); для свободных запросов к логам нужна роль Editor. Это ограничение интерфейса: в Grafana OSS любой вошедший пользователь может выполнить запрос к источникам данных через HTTP API (`/api/ds/query`), поэтому учётку Grafana выдают только тем, кому допустимо видеть логи целиком.
  * Первичный выпуск сертификата для поддомена: `docker compose -f docker-compose.yml -f docker-compose.prod.yml run --rm --entrypoint certbot certbot certonly --webroot -w /var/www/certbot -d grafana.getbalanceai.app`.
  * **Смена версии Grafana:** новая версия при старте необратимо мигрирует базу в томе `grafana_data`, поэтому до мержа PR с новым образом на сервере выполняется `./scripts/backup_grafana.sh` (Grafana останавливается на несколько секунд; архив `backups/grafana_backup_*.tar.gz`, хранятся 3 последних). Откат: вернуть прежний тег образа в `docker-compose.yml`, затем восстановить том из архива:
    ```bash
    dc() { docker compose -f docker-compose.yml -f docker-compose.prod.yml "$@"; }
    dc stop grafana
    dc run --rm --no-deps -T --entrypoint sh grafana -c 'rm -rf /var/lib/grafana/* && tar xzf - -C /var/lib/grafana' < backups/grafana_backup_<время>.tar.gz
    dc up -d grafana
    ```
* **Loki + Promtail** — автоматический сбор всех логов контейнеров.
* **Prometheus + cAdvisor** — сбор системных метрик контейнеров (CPU, RAM) и метрик самого приложения FastAPI (`/metrics`).

Базовые дашборды для метрик и логов уже преднастроены и доступны в Grafana сразу после запуска контейнеров!

### Доступ к служебным ресурсам на проде (basic auth)
Grafana и документация API (`/docs`, `/redoc`, `/openapi.json`) закрыты в nginx логином и паролем basic auth — это отдельный вход перед логином Grafana. Учётки хранятся в `nginx/htpasswd` на сервере (в git не попадает); без этого файла деплой останавливается.

```bash
cd /opt/getbalanceai_api
# Добавить учётку (по одной на человека; пароль сохраняется bcrypt-хешем)
docker run --rm httpd:2.4-alpine htpasswd -nbB <логин> '<пароль>' >> nginx/htpasswd
chmod 644 nginx/htpasswd
# Отозвать доступ — удалить строку пользователя
sed -i '/^<логин>:/d' nginx/htpasswd
```

Изменения файла применяются сразу, перезапуск nginx не нужен. В Postman схема импортируется по ссылке `https://api.getbalanceai.app/openapi.json` с авторизацией Basic Auth.

Снаружи сервер отвечает только на `api.getbalanceai.app` и `grafana.getbalanceai.app` (порты 80/443); запросы по IP отклоняются. Остальные порты контейнеров опубликованы лишь на `127.0.0.1`. Порт API задаётся `APP_BIND` (по умолчанию `127.0.0.1`); `0.0.0.0` допустим только локально, например для теста с телефона. Монтирование кода в контейнер (`docker-compose.override.yml`) подключается только при локальном `docker compose` без `-f`, поэтому на сервере все команды выполняются с `-f docker-compose.yml -f docker-compose.prod.yml`.

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
2. **CI (`.github/workflows/main.yml`):** линтеры и тесты на каждый PR и push в `main`; автодеплой запускается только после успешного CI. В `Dockerfile` тесты не выполняются.
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

## 🗑️ Удаление аккаунта по запросу на email

Страница `/delete-account` обещает удаление данных в течение 30 дней по письму с адреса регистрации. Администратор выполняет его на сервере (после проверки, что письмо пришло с адреса аккаунта):
```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml exec api python scripts/delete_user_by_email.py --email user@example.com
```
Скрипт ищет пользователя без учёта регистра, просит повторно ввести email (флаг `--yes` пропускает подтверждение) и удаляет аккаунт теми же шагами, что и `DELETE /api/v1/auth/me`. Коды возврата: `0` — удалён, `1` — не найден, `2` — найдено несколько пользователей, `3` — отменено.


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

## 🤖 Регламент разработки для AI-агентов

Стандарты работы агентов, правила ветвления, слои архитектуры и чек-листы проверок зафиксированы в [GEMINI.md](GEMINI.md) и контекстных файлах слоев (`tests/GEMINI.md`, `app/routers/GEMINI.md`, `app/services/GEMINI.md`, `app/models/GEMINI.md`, `migrations/GEMINI.md`).
