#!/bin/bash
# Резервная копия тома grafana_data (SQLite-база пользователей, ролей, настроек) перед обновлением Grafana.
# Новая версия при старте необратимо мигрирует схему базы, поэтому откат на старый образ возможен только
# восстановлением этой копии (см. README, раздел Мониторинг). Хранятся 3 последние копии.
# Использование: backup_grafana.sh

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )"
PROJECT_DIR="${SCRIPT_DIR}/.."
BACKUP_DIR="${PROJECT_DIR}/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="${BACKUP_DIR}/grafana_backup_${TIMESTAMP}.tar.gz"

# В базе Grafana хэши паролей пользователей: каталог и файлы доступны только владельцу
umask 077
mkdir -p "$BACKUP_DIR"
cd "$PROJECT_DIR"

# Grafana останавливается на время копирования: копия SQLite-файла под записью может оказаться несогласованной
docker compose stop grafana
trap 'docker compose start grafana' EXIT

echo "Копируем том grafana_data..."
# Архив собирается внутри одноразового контейнера сервиса grafana: ему смонтирован тот же том, имя тома знать не нужно.
# stdin закрыт: run иначе забирает его себе
if ! docker compose run --rm --no-deps -T --entrypoint tar grafana czf - -C /var/lib/grafana . </dev/null > "$BACKUP_FILE"; then
    echo "Ошибка резервного копирования Grafana!"
    rm -f "$BACKUP_FILE"
    exit 1
fi

echo "Резервное копирование Grafana завершено: $BACKUP_FILE"
ls -t "$BACKUP_DIR"/grafana_backup_*.tar.gz | tail -n +4 | xargs -r rm --
