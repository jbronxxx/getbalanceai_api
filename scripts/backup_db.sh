#!/bin/bash
# Скрипт для создания резервных копий базы данных (pg_dump)
# Использование: backup_db.sh [daily|predeploy] (по умолчанию daily)
#   daily     — регулярная копия из cron, хранятся 7 последних файлов (≈7 суток при ежедневном запуске)
#   predeploy — копия перед миграциями при деплое, хранятся не более 3 файлов и не дольше 7 суток

MODE="${1:-daily}"
case "$MODE" in
    daily) PREFIX="db_backup" ;;
    predeploy) PREFIX="predeploy_backup" ;;
    *)
        echo "Неизвестный режим: $MODE (ожидается daily или predeploy)"
        exit 2
        ;;
esac

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="${SCRIPT_DIR}/.."
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/${PREFIX}_${TIMESTAMP}.sql"

# Дампы содержат персональные данные: каталог и файлы доступны только владельцу
umask 077
mkdir -p "$BACKUP_DIR"

echo "Начинаем создание резервной копии базы данных ($MODE)..."
cd "$PROJECT_DIR"

# Мы используем 'docker compose exec -T' чтобы можно было запускать скрипт из cron (без TTY).
# Пользователь и имя БД берутся из окружения контейнера (POSTGRES_USER/POSTGRES_DB из .env), а не зашиты в скрипт.
# stdin закрыт: exec иначе забирает его себе, а при вызове из деплоя это остаток скрипта деплоя
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' </dev/null > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Резервное копирование успешно завершено: $BACKUP_FILE"
    if [ "$MODE" = "daily" ]; then
        # Оставляем только последние 7 бэкапов, чтобы не переполнять диск
        ls -t "$BACKUP_DIR"/db_backup_*.sql | tail -n +8 | xargs -r rm --
    else
        # Срок хранения ограничен Политикой конфиденциальности (не более 7 суток), количество — местом на диске
        find "$BACKUP_DIR" -name 'predeploy_backup_*.sql' -mtime +6 -delete
        ls -t "$BACKUP_DIR"/predeploy_backup_*.sql | tail -n +4 | xargs -r rm --
    fi
else
    echo "Ошибка резервного копирования!"
    rm -f "$BACKUP_FILE"
    exit 1
fi
