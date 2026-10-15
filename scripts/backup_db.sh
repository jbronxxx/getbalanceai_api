#!/bin/bash
# Скрипт для создания резервных копий базы данных (pg_dump)

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="${SCRIPT_DIR}/.."
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/db_backup_${TIMESTAMP}.sql"

mkdir -p "$BACKUP_DIR"

echo "Начинаем создание резервной копии базы данных..."
cd "$PROJECT_DIR"

# Мы используем 'docker compose exec -T' чтобы можно было запускать скрипт из cron (без TTY).
# Пользователь и имя БД берутся из окружения контейнера (POSTGRES_USER/POSTGRES_DB из .env), а не зашиты в скрипт
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$BACKUP_FILE"

if [ $? -eq 0 ]; then
    echo "Резервное копирование успешно завершено: $BACKUP_FILE"
    # Оставляем только последние 7 бэкапов, чтобы не переполнять диск
    ls -t "$BACKUP_DIR"/db_backup_*.sql | tail -n +8 | xargs -r rm --
else
    echo "Ошибка резервного копирования!"
    rm -f "$BACKUP_FILE"
    exit 1
fi
