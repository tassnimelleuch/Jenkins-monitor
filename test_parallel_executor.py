"""Run real parallel API calls to observe thread behavior."""

import threading
import time
from datetime import datetime

from flask import Flask

from config import Config
from services.parallel_executor import parallel_execute
from services.dashboard_service import get_pipeline_kpis
from services.deployment_kpis_service import get_deployment_kpis
from services.github_service import get_github_summary
from services.sonarcloud_service import get_sonarcloud_summary


def _now():
    return datetime.now().strftime("%H:%M:%S.%f")[:-3]


def log_event(task_name: str, event: str, duration: float | None = None):
    thread_name = threading.current_thread().name
    if duration is None:
        print(f"[{_now()}] {thread_name:15} | {task_name:24} | {event}")
    else:
        print(f"[{_now()}] {thread_name:15} | {task_name:24} | {event} ({duration:.2f}s)")


def _timed(task_name: str, func):
    log_event(task_name, "start")
    start = time.time()
    result = func()
    elapsed = time.time() - start
    log_event(task_name, "done", elapsed)
    return {
        "task": task_name,
        "duration": elapsed,
        "thread": threading.current_thread().name,
        "result": result,
    }


def _run_in_app_context(app: Flask, func):
    with app.app_context():
        return func()


def build_tasks(app: Flask):
    return {
        "pipeline_kpis": lambda: _timed(
            "pipeline_kpis", lambda: _run_in_app_context(app, get_pipeline_kpis)
        ),
        "deployment_kpis": lambda: _timed(
            "deployment_kpis", lambda: _run_in_app_context(app, get_deployment_kpis)
        ),
        "github_summary": lambda: _timed(
            "github_summary", lambda: _run_in_app_context(app, get_github_summary)
        ),
        "sonarcloud_summary": lambda: _timed(
            "sonarcloud_summary", lambda: _run_in_app_context(app, get_sonarcloud_summary)
        ),
    }


def main():
    app = Flask(__name__)
    app.config.from_object(Config)

    print("\n=== PARALLEL EXECUTOR: REAL API CALLS ===\n")
    tasks = build_tasks(app)
    start = time.time()
    results = parallel_execute(tasks, max_workers=4, timeout=60)
    total = time.time() - start

    print("\nResults:")
    for key, value in results.items():
        if value is None:
            print(f"- {key}: FAILED")
        else:
            print(f"- {key}: {value['duration']:.2f}s on {value['thread']}")
    print(f"\nTotal time: {total:.2f}s\n")


if __name__ == "__main__":
    main()
