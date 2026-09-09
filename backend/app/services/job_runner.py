"""
Background jobs with progress, for work too slow to hold a request open.

Reconciling one day matches every payment against every settlement and takes
around a minute. A request that blocks for that long looks broken however good
the spinner is, and the operator has no idea whether it is nearly done or
stuck.

So the work runs on a thread and reports where it is. Real steps, not a
timed animation: a progress bar that moves on a timer is a lie, and the first
time it finishes while the work is still running nobody trusts it again.

In-process and in-memory. A restart loses running jobs, which is correct for
something this cheap to re-run -- persisting them would mean recovering jobs
whose thread died, for no gain.
"""

from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Callable

# Finished jobs are kept briefly so the page can collect the result after the
# final poll, then dropped -- these payloads are large.
_TTL_SECONDS = 15 * 60

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


def _prune() -> None:
    now = time.time()
    for job_id, job in list(_jobs.items()):
        if job.get("finished_at") and now - job["finished_at"] > _TTL_SECONDS:
            _jobs.pop(job_id, None)


def start_job(app, work: Callable[[Callable[[str, int], None]], Any], steps: list[str]) -> str:
    """
    Run `work` on a thread, giving it a `progress(step_name, index)` callback.

    `steps` is the plan, sent to the client up front so it can show the whole
    timeline rather than only the step in hand -- knowing there are five stages
    is most of what makes a minute bearable.
    """
    job_id = uuid.uuid4().hex
    with _lock:
        _prune()
        _jobs[job_id] = {
            "id": job_id, "steps": steps, "step": steps[0] if steps else "",
            "step_index": 0, "started_at": time.time(), "finished_at": None,
            "done": False, "error": None, "result": None,
        }

    def progress(step: str, index: int) -> None:
        with _lock:
            job = _jobs.get(job_id)
            if job:
                job["step"] = step
                job["step_index"] = index

    def run() -> None:
        # The worker touches the database and the switch, both of which need an
        # application context; a thread does not inherit the request's.
        with app.app_context():
            try:
                result = work(progress)
                with _lock:
                    job = _jobs.get(job_id)
                    if job:
                        job.update(result=result, done=True, step_index=len(steps),
                                   step="Done", finished_at=time.time())
            except Exception as exc:  # noqa: BLE001 - surfaced to the operator
                with _lock:
                    job = _jobs.get(job_id)
                    if job:
                        job.update(error=str(exc).strip().splitlines()[0][:300],
                                   done=True, finished_at=time.time())

    threading.Thread(target=run, name=f"job-{job_id[:8]}", daemon=True).start()
    return job_id


def job_status(job_id: str, include_result: bool = True) -> dict | None:
    """Where a job is. The result is only attached once it has finished."""
    with _lock:
        job = _jobs.get(job_id)
        if job is None:
            return None
        out = {
            "id": job["id"], "steps": job["steps"], "step": job["step"],
            "step_index": job["step_index"], "done": job["done"],
            "error": job["error"],
            "elapsed_seconds": round(
                (job["finished_at"] or time.time()) - job["started_at"], 1
            ),
        }
        if include_result and job["done"] and job["error"] is None:
            out["result"] = job["result"]
        return out
