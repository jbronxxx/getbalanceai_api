"""Хуки gunicorn для метрик Prometheus в режиме нескольких воркеров.

Каждый воркер — отдельный процесс со своим реестром метрик, а Prometheus при каждом сборе попадает
в случайный воркер: счётчики «прыгали», графики rate() были неверными. В multiprocess-режиме
prometheus_client пишет значения всех воркеров в общий каталог PROMETHEUS_MULTIPROC_DIR,
и /metrics отдаёт их сумму.
"""

import os
import shutil

from prometheus_client import multiprocess


def on_starting(server) -> None:
    """Перед запуском воркеров очистить каталог: файлы прошлого запуска исказили бы счётчики."""
    metrics_dir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
    if metrics_dir:
        shutil.rmtree(metrics_dir, ignore_errors=True)
        os.makedirs(metrics_dir, exist_ok=True)


def child_exit(server, worker) -> None:
    """Пометить метрики завершившегося воркера: его gauge-значения перестают учитываться."""
    if os.environ.get("PROMETHEUS_MULTIPROC_DIR"):
        multiprocess.mark_process_dead(worker.pid)
