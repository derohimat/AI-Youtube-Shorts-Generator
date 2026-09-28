"""Background jobs (analyze / render) that survive closing the browser tab.

One worker runs jobs in order. Each project's current job is stored in
work/<id>/job.json so any page (or a page opened later) can show its progress:

    {"kind": "analyze" | "render", "status": "queued" | "running" | "done" | "error" | "interrupted",
     "progress": 0.0-1.0, "message": "...", "error": "...", "params": {...},
     "queued_at": ..., "started_at": ..., "finished_at": ...}

Jobs do not survive a server restart; `recover()` marks them "interrupted" so they can be retried.
"""
import json
import os
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

from Components import config

JOB_FILE = "job.json"
ACTIVE = ("queued", "running")

_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="shorts-job")
_LOCK = threading.Lock()


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def read(work_dir):
    path = os.path.join(work_dir, JOB_FILE)
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write(work_dir, job):
    from Components.pipeline import _write_json
    if os.path.isdir(work_dir):  # the project may have been deleted meanwhile
        _write_json(os.path.join(work_dir, JOB_FILE), job)


def _update(work_dir, **fields):
    with _LOCK:
        job = read(work_dir) or {}
        job.update(fields)
        _write(work_dir, job)
        return job


def is_active(job):
    return bool(job) and job.get("status") in ACTIVE


def submit(work_dir, kind, fn, params=None):
    """Queue `fn(progress=callback)` for the project in `work_dir`. Raises if a job is already active."""
    os.makedirs(work_dir, exist_ok=True)
    with _LOCK:
        current = read(work_dir)
        if is_active(current):
            raise RuntimeError(f"A {current['kind']} job is already running for this project.")
        job = {"kind": kind, "status": "queued", "progress": 0.0, "message": "Waiting in queue...",
               "error": None, "params": params or {}, "queued_at": _now(), "started_at": None,
               "finished_at": None}
        _write(work_dir, job)

    def run():
        _update(work_dir, status="running", started_at=_now(), message="Starting...")
        last = [0.0]

        def progress(fraction, message):
            now = time.time()
            if now - last[0] >= 1.0 or fraction >= 1.0:  # throttle disk writes
                last[0] = now
                _update(work_dir, progress=round(max(0.0, min(1.0, float(fraction))), 3), message=str(message))

        try:
            fn(progress=progress)
        except Exception as e:  # noqa: BLE001 - every failure must be reported to the UI
            traceback.print_exc()
            _update(work_dir, status="error", error=f"{type(e).__name__}: {e}", finished_at=_now())
        else:
            _update(work_dir, status="done", progress=1.0, message="Done", finished_at=_now())

    _EXECUTOR.submit(run)
    return job


def recover(work_root=None):
    """Mark jobs left 'queued'/'running' by a previous server process as interrupted."""
    work_root = work_root or config.WORK_DIR
    if not os.path.isdir(work_root):
        return 0
    count = 0
    for name in os.listdir(work_root):
        work_dir = os.path.join(work_root, name)
        job = read(work_dir)
        if is_active(job):
            _update(work_dir, status="interrupted", finished_at=_now(),
                    error="The server restarted before this job finished.")
            count += 1
    return count


def describe(job):
    """Short human-readable (Indonesian) status line for the UI."""
    if not job:
        return ""
    kind = "Analisis" if job["kind"] == "analyze" else "Render"
    status = job["status"]
    if status == "queued":
        return f"⏳ {kind}: menunggu antrian..."
    if status == "running":
        return f"⏳ {kind} {job['progress'] * 100:.0f}% · {job.get('message', '')}"
    if status == "done":
        return f"✅ {kind} selesai ({job.get('finished_at', '')})"
    if status == "interrupted":
        return f"⚠️ {kind} terhenti karena server di-restart. Klik **Coba lagi**."
    return f"❌ {kind} gagal: {job.get('error', '')}"
