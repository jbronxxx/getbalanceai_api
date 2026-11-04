#!/usr/bin/env bash
# Запуск сценария нагрузочного теста: ход теста — в терминал в реальном времени,
# отчёты — в reports/<дата_время>_<сценарий>/, чтобы следующие прогоны не перезаписывали предыдущие.
#
# Использование: ./run.sh <сценарий> [дополнительные флаги locust]
# Сценарии: smoke, steps, soak, spike, double_spike, offline, cold_start, stress.
# Переменные: HOST (по умолчанию https://api.getbalanceai.app), LOADTEST_TOKENS и пороги из locustfile.py.
set -euo pipefail

usage() {
    echo "Использование: $0 <smoke|steps|soak|spike|double_spike|offline|cold_start|stress> [флаги locust]" >&2
}

if [[ $# -lt 1 ]]; then
    usage
    exit 2
fi

SCENARIO="$1"
shift
HOST="${HOST:-https://api.getbalanceai.app}"
PROCESSES=(--processes 4)

case "$SCENARIO" in
    smoke)
        ARGS=(-u 2 -r 1 -t 1m)
        ;;
    steps | soak)
        export LOADTEST_SHAPE="$SCENARIO"
        ARGS=("${PROCESSES[@]}")
        ;;
    spike | double_spike)
        export LOADTEST_SHAPE="$SCENARIO"
        export LOADTEST_MAX_FAIL_RATIO="${LOADTEST_MAX_FAIL_RATIO:-0.01}"
        ARGS=("${PROCESSES[@]}")
        ;;
    cold_start)
        # Тот же всплеск, но сразу после перезапуска API со сброшенным кэшем курсов (см. README.md)
        export LOADTEST_SHAPE=spike
        export LOADTEST_MAX_FAIL_RATIO="${LOADTEST_MAX_FAIL_RATIO:-0.01}"
        ARGS=("${PROCESSES[@]}")
        ;;
    offline)
        # Без --processes: в Locust 2.46 выбор класса пользователя вместе с --processes роняет воркеры (KeyError),
        # а 50 пользователей с паузой 30–60 с одному процессу по силам
        export LOADTEST_OFFLINE_BATCH="${LOADTEST_OFFLINE_BATCH:-200-500}"
        ARGS=(-u 50 -r 10 -t 5m OfflineSyncUser)
        ;;
    stress)
        # Цель — найти точку отказа, поэтому ошибки не считаются нарушением критериев
        export LOADTEST_SHAPE=stress
        export LOADTEST_MAX_FAIL_RATIO="${LOADTEST_MAX_FAIL_RATIO:-1}"
        ARGS=("${PROCESSES[@]}")
        ;;
    *)
        usage
        exit 2
        ;;
esac

cd "$(dirname "$0")"
LOCUST_BIN="locust"
if [[ -x .venv/bin/locust ]]; then
    LOCUST_BIN=".venv/bin/locust"
fi

REPORT_DIR="reports/$(date +%Y-%m-%d_%H-%M-%S)_${SCENARIO}"
mkdir -p "$REPORT_DIR"
echo "Сценарий: $SCENARIO, хост: $HOST, отчёты: loadtest/$REPORT_DIR"

# Без буферизации вывод Python через tee доходит до терминала сразу, а не пачками
export PYTHONUNBUFFERED=1
# На macOS прогон держит систему без сна: заснувший Mac останавливает Locust, и тест обрывается
KEEP_AWAKE=()
if command -v caffeinate >/dev/null 2>&1; then
    KEEP_AWAKE=(caffeinate -dims)
fi
set +e
${KEEP_AWAKE[@]+"${KEEP_AWAKE[@]}"} "$LOCUST_BIN" -f locustfile.py --host "$HOST" --headless "${ARGS[@]}" "$@" \
    --html "$REPORT_DIR/report.html" \
    --csv "$REPORT_DIR/stats" --csv-full-history \
    2>&1 | tee "$REPORT_DIR/console.log"
EXIT_CODE=${PIPESTATUS[0]}
set -e

echo "Код завершения: $EXIT_CODE (0 — критерии успеха выполнены). Отчёт: loadtest/$REPORT_DIR/report.html"
exit "$EXIT_CODE"
