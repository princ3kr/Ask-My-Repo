import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class JobStatus:
    job_id: str
    stage: str = "starting"
    progress: int = 0
    message: str = "Getting things ready…"
    status: str = "running"
    result: dict[str, Any] | None = None
    error: str | None = None


_jobs: dict[str, JobStatus] = {}
_lock = threading.Lock()

ProgressCallback = Callable[..., None]


def create_job() -> str:
    job_id = str(uuid.uuid4())
    with _lock:
        _jobs[job_id] = JobStatus(job_id=job_id)
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
        )


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
