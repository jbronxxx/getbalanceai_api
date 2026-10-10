#!/usr/bin/env bash
# ==============================================================================
# Скрипт локального развертывания проекта для разработки (macOS / Linux)
# Использование: ./scripts/setup_dev.sh
# Или:           source ./scripts/setup_dev.sh (для сохранения активации .venv в текущей оболочке)
# ==============================================================================

set -e

# Переход в корневую директорию проекта
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &>/dev/null && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." &>/dev/null && pwd)"
cd "$PROJECT_DIR"

echo -e "\033[36m==> Рабочая директория: $PROJECT_DIR\033[0m"

# ------------------------------------------------------------------------------
# 1. Проверка и создание .env
# ------------------------------------------------------------------------------
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        echo -e "\033[33m==> Создание .env из .env.example...\033[0m"
        cp .env.example .env
    else
        echo -e "\033[31m==> Внимание: .env.example не найден. Создайте .env вручную.\033[0m"
    fi
else
    echo -e "\033[32m==> Файл .env уже существует.\033[0m"
fi

# ------------------------------------------------------------------------------
# 2. Создание и активация виртуального окружения (.venv)
# ------------------------------------------------------------------------------
# Проверка версии существующего .venv (требуется >= 3.11 для enum.StrEnum)
if [ -f ".venv/bin/python" ]; then
    PY_VER=$(.venv/bin/python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
    MAJOR=$(echo "$PY_VER" | cut -d. -f1)
    MINOR=$(echo "$PY_VER" | cut -d. -f2)
    if [ "$MAJOR" -lt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 11 ]); then
        echo -e "\033[33m==> Текущий .venv создан на Python $PY_VER, требуется Python 3.11+. Пересоздаем .venv...\033[0m"
        rm -rf .venv
    fi
fi

if [ ! -f ".venv/bin/python" ]; then
    echo -e "\033[33m==> Создание виртуального окружения (.venv)...\033[0m"
    PYTHON_CMD=""
    for cmd in python3.12 python3.11 python3 python; do
        if command -v "$cmd" &>/dev/null; then
            PY_VER=$($cmd -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')" 2>/dev/null || echo "0.0")
            MAJOR=$(echo "$PY_VER" | cut -d. -f1)
            MINOR=$(echo "$PY_VER" | cut -d. -f2)
            if [ "$MAJOR" -gt 3 ] || ([ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 11 ]); then
                PYTHON_CMD="$cmd"
                break
            fi
        fi
    done

    if [ -z "$PYTHON_CMD" ]; then
        echo -e "\033[31mОшибка: Не найден Python 3.11+. Установите Python 3.11 или 3.12 (в Dockerfile используется Python 3.12).\033[0m"
        exit 1
    fi

    echo "    Выбран интерпретатор: $PYTHON_CMD ($PY_VER)"
    "$PYTHON_CMD" -m venv .venv
fi

echo -e "\033[32m==> Активация виртуального окружения...\033[0m"
source .venv/bin/activate

# ------------------------------------------------------------------------------
# 3. Обновление pip и установка зависимостей
# ------------------------------------------------------------------------------
echo -e "\033[33m==> Обновление pip...\033[0m"
pip install --upgrade pip

echo -e "\033[33m==> Установка основных зависимостей (requirements.txt)...\033[0m"
pip install --prefer-binary -r requirements.txt

if [ -f "requirements-dev.txt" ]; then
    echo -e "\033[33m==> Установка зависимостей для разработки (requirements-dev.txt)...\033[0m"
    pip install -r requirements-dev.txt
fi

if command -v pre-commit &>/dev/null; then
    echo -e "\033[33m==> Настройка pre-commit хуков git...\033[0m"
    pre-commit install || true
    pre-commit install --hook-type pre-push || true
fi

# ------------------------------------------------------------------------------
# 4. Запуск контейнеров БД и Redis (Docker)
# ------------------------------------------------------------------------------
echo -e "\033[33m==> Проверка Docker...\033[0m"
if ! docker info &>/dev/null; then
    echo -e "\033[31mОшибка: Docker демон не запущен. Запустите Docker Desktop / daemon.\033[0m"
    exit 1
fi

echo -e "\033[33m==> Запуск контейнеров инфраструктуры (PostgreSQL и Redis)...\033[0m"
docker compose up -d db redis

# Считываем переменные БД из .env с дефолтными значениями
DB_USER=$(grep -E '^POSTGRES_USER=' .env 2>/dev/null | cut -d '=' -f2- | tr -d '\r' || echo "db_user")
DB_PASS=$(grep -E '^POSTGRES_PASSWORD=' .env 2>/dev/null | cut -d '=' -f2- | tr -d '\r' || echo "db_pass")
DB_NAME=$(grep -E '^POSTGRES_DB=' .env 2>/dev/null | cut -d '=' -f2- | tr -d '\r' || echo "get_balance_db")
DB_PORT=$(grep -E '^POSTGRES_PORT=' .env 2>/dev/null | cut -d '=' -f2- | tr -d '\r' || echo "5432")

DB_USER=${DB_USER:-db_user}
DB_PASS=${DB_PASS:-db_pass}
DB_NAME=${DB_NAME:-get_balance_db}
DB_PORT=${DB_PORT:-5432}

# Ожидание готовности PostgreSQL
echo -e "\033[33m==> Ожидание готовности PostgreSQL к приему подключений...\033[0m"
MAX_RETRIES=30
RETRY_COUNT=0
DB_READY=0

while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    if docker compose exec -T db pg_isready -U "$DB_USER" -d "$DB_NAME" &>/dev/null; then
        DB_READY=1
        echo -e "\033[32m==> PostgreSQL готов к работе!\033[0m"
        break
    fi
    RETRY_COUNT=$((RETRY_COUNT + 1))
    echo "    Ожидание базы данных... ($RETRY_COUNT/$MAX_RETRIES)"
    sleep 1
done

if [ $DB_READY -ne 1 ]; then
    echo -e "\033[31mОшибка: Таймаут ожидания запуска PostgreSQL. Проверьте логи: docker compose logs db\033[0m"
    exit 1
fi

# ------------------------------------------------------------------------------
# 5. Применение миграций базы данных (Alembic)
# ------------------------------------------------------------------------------
echo -e "\033[33m==> Применение миграций БД (Alembic)...\033[0m"
# Передаем DATABASE_URL с хостом 127.0.0.1 для локального подключения
DATABASE_URL="postgresql://${DB_USER}:${DB_PASS}@127.0.0.1:${DB_PORT}/${DB_NAME}" alembic upgrade head

echo -e "\033[32m==> Миграции успешно применены!\033[0m"

# ------------------------------------------------------------------------------
# 6. Итоги и инструкции
# ------------------------------------------------------------------------------
echo ""
echo -e "\033[36m============================================================\033[0m"
echo -e "\033[32m Развертывание локального окружения успешно завершено!      \033[0m"
echo -e "\033[36m============================================================\033[0m"
echo "1. Инфраструктура (PostgreSQL, Redis) запущена в Docker."
echo "2. Виртуальное окружение активировано: $PROJECT_DIR/.venv"
echo "3. Все зависимости и git hooks установлены."
echo "4. Миграции БД применены."
echo ""
echo -e "\033[33mДля запуска сервера разработки выполните:\033[0m"
echo "  uvicorn main:app --reload --host 0.0.0.0 --port 8000"
echo ""
echo -e "\033[33mДля запуска тестов:\033[0m"
echo "  pytest"
echo -e "\033[36m============================================================\033[0m"
