"""Job status store, and the API surface without live databases."""
import threading
import time

import pytest

from src.backend import job_status


@pytest.fixture(autouse=True)
def _clean_jobs():
    with job_status._lock:
        job_status._jobs.clear()
    yield
    with job_status._lock:
        job_status._jobs.clear()


class TestJobLifecycle:
    def test_create_then_read(self):
        jid = job_status.create_job()
        job = job_status.get_job(jid)
        assert job is not None
        assert job.status == "running"
        assert job.progress == 0

    def test_update_fields(self):
        jid = job_status.create_job()
        job_status.update_job(
            jid, stage="fetching", progress=42, message="hi", status="running"
        )
        job = job_status.get_job(jid)
        assert (job.stage, job.progress, job.message) == ("fetching", 42, "hi")

    def test_progress_is_clamped(self):
        jid = job_status.create_job()
        job_status.update_job(jid, progress=500)
        assert job_status.get_job(jid).progress == 100
        job_status.update_job(jid, progress=-10)
        assert job_status.get_job(jid).progress == 0

    def test_unknown_job_is_a_noop(self):
        job_status.update_job("nope", progress=1)  # must not raise
        assert job_status.get_job("nope") is None

    def test_result_is_copied_not_aliased(self):
        jid = job_status.create_job()
        job_status.update_job(jid, result={"repo_id": "r"})
        first = job_status.get_job(jid)
        first.result["repo_id"] = "mutated"
        assert job_status.get_job(jid).result["repo_id"] == "r"

    def test_job_to_dict_omits_empty_optionals(self):
        jid = job_status.create_job()
        d = job_status.job_to_dict(job_status.get_job(jid))
        assert set(d) == {"job_id", "stage", "progress", "message", "status"}
        assert "result" not in d
        assert "error" not in d

    def test_job_to_dict_includes_result_and_error(self):
        jid = job_status.create_job()
        job_status.update_job(jid, status="done", result={"a": 1}, error="e")
        d = job_status.job_to_dict(job_status.get_job(jid))
        assert d["result"] == {"a": 1}
        assert d["error"] == "e"


class TestStopAllJobs:
    def test_marks_running_jobs_failed(self):
        running = job_status.create_job()
        done = job_status.create_job()
        job_status.update_job(done, status="done")

        job_status.stop_all_jobs()

        assert job_status.get_job(running).status == "error"
        assert job_status.get_job(running).error
        assert job_status.get_job(done).status == "done", "finished jobs must be left alone"


class TestStoreIsBounded:
    def test_sweep_drops_finished_jobs(self):
        finished = job_status.create_job()
        job_status.update_job(finished, status="done")
        job_status.create_job()  # triggers a sweep

        assert job_status.get_job(finished) is None
        assert job_status.job_count() == 1

    def test_sweep_drops_expired_running_jobs(self):
        jid = job_status.create_job()
        with job_status._lock:
            job_status._jobs[jid].created_at = time.time() - (job_status.JOB_TTL_SECONDS + 60)
        job_status.create_job()  # triggers a sweep
        assert job_status.get_job(jid) is None

    def test_store_never_exceeds_the_cap(self):
        """`_jobs` was an unbounded dict, so every parse job lived for the
        life of the process even after its result had been read."""
        original_max = job_status.MAX_JOBS
        job_status.MAX_JOBS = 20
        try:
            for _ in range(60):
                job_status.create_job()
            assert job_status.job_count() <= 20
        finally:
            job_status.MAX_JOBS = original_max

    def test_concurrent_creates_are_all_recorded(self):
        errors = []
        ids = []

        def worker():
            try:
                for _ in range(50):
                    ids.append(job_status.create_job())
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(set(ids)) == len(ids) == 300
