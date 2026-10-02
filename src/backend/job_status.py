import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# Jobs are only ever read back by job_id, and a client that abandons a parse
# never polls to completion, so entries accumulated for the life of the
# process. Bounded with a TTL sweep instead.
JOB_TTL_SECONDS = 6 * 60 * 60
MAX_JOBS = 500


@dataclass
class JobStatus:
    job_id: str
    stage: str = "starting"
    progress: int = 0
    message: str = "Getting things ready…"
    status: str = "running"
    result: dict[str, Any] | None = None
    error: str | None = None
    created_at: float = 0.0


_jobs: dict[str, JobStatus] = {}
_lock = threading.Lock()

ProgressCallback = Callable[..., None]


def _sweep_locked(now: float) -> None:
    """Drop finished and expired jobs. Caller must hold _lock."""
    for job_id in [j for j, s in _jobs.items() if s.status != "running"]:
        _jobs.pop(job_id, None)
    for job_id in [
        j for j, s in _jobs.items()
        if s.created_at and now - s.created_at > JOB_TTL_SECONDS
    ]:
        _jobs.pop(job_id, None)

    # Still over the cap (many concurrent live jobs): evict the oldest running
    # ones, which are the least likely to be polled again.
    overflow = len(_jobs) - MAX_JOBS
    if overflow > 0:
        for job_id, _ in sorted(_jobs.items(), key=lambda kv: kv[1].created_at)[:overflow]:
            _jobs.pop(job_id, None)


def create_job() -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        now = time.time()
        _jobs[job_id] = JobStatus(job_id=job_id, created_at=now)
        # Swept after insertion so the cap holds exactly rather than
        # oscillating at MAX_JOBS + 1.
        _sweep_locked(now)
    return job_id


def update_job(
    job_id: str,
    *,
    stage: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    status: str | None = None,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return
        if stage is not None:
            job.stage = stage
        if progress is not None:
            job.progress = max(0, min(100, progress))
        if message is not None:
            job.message = message
        if status is not None:
            job.status = status
        if result is not None:
            job.result = result
        if error is not None:
            job.error = error


def get_job(job_id: str) -> JobStatus | None:
    with _lock:
        job = _jobs.get(job_id)
        if not job:
            return None
        return JobStatus(
            job_id=job.job_id,
            stage=job.stage,
            progress=job.progress,
            message=job.message,
            status=job.status,
            result=job.result.copy() if job.result else None,
            error=job.error,
            created_at=job.created_at,
        )


def stop_all_jobs() -> None:
    """Mark every in-flight job failed. Called on server shutdown."""
    with _lock:
        for job in _jobs.values():
            if job.status == "running":
                job.status = "error"
                job.stage = "error"
                job.error = "Server shut down before this job finished."
                job.message = job.error


def job_count() -> int:
    with _lock:
        return len(_jobs)


def job_to_dict(job: JobStatus) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "job_id": job.job_id,
        "stage": job.stage,
        "progress": job.progress,
        "message": job.message,
        "status": job.status,
    }
    if job.result:
        payload["result"] = job.result
    if job.error:
        payload["error"] = job.error
    return payload
