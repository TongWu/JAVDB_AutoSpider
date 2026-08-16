"""Path-traversal safety for job_store (extracted from task_service).

job_store owns every filesystem path a task job touches under
context.RESOLVED_JOB_LOG_DIR. These pin the security-critical guards directly
on the module (independent of task_service's re-exports) so a future edit that
weakens job-id validation or lets a path escape the job log dir fails here.
"""

import pytest
from fastapi import HTTPException

from apps.api.services import context, job_store


class TestValidateJobId:
    @pytest.mark.parametrize(
        "job_id", ["daily-20260614-120000-ab", "adhoc-1", "a", "A_b-9", "x" * 64]
    )
    def test_accepts_valid(self, job_id):
        job_store.validate_job_id(job_id)  # must not raise

    @pytest.mark.parametrize(
        "job_id",
        ["", "../etc/passwd", "a/b", "a.b", "a b", "x" * 65, "a;b", ".", ".."],
    )
    def test_rejects_invalid(self, job_id):
        with pytest.raises(HTTPException) as exc:
            job_store.validate_job_id(job_id)
        assert exc.value.status_code == 422


class TestSafeJobLogFilename:
    def test_builds_single_part_name(self):
        assert job_store.safe_job_log_filename("daily-1", ".log") == "daily-1.log"

    def test_rejects_extension_without_dot(self):
        with pytest.raises(HTTPException) as exc:
            job_store.safe_job_log_filename("daily-1", "log")
        assert exc.value.status_code == 500

    def test_rejects_traversal_job_id(self):
        with pytest.raises(HTTPException):
            job_store.safe_job_log_filename("../evil", ".log")


class TestResolvedPathStaysUnderDir:
    def test_resolved_path_is_inside_job_log_dir(self):
        path = job_store.resolved_path_under_job_log_dir("daily-1", ".log")
        # Raises ValueError (caught by the test) if it escaped the dir.
        path.relative_to(context.RESOLVED_JOB_LOG_DIR)

    def test_safe_log_path_has_log_extension(self):
        assert job_store.safe_log_path("daily-1").name == "daily-1.log"

    def test_job_meta_path_has_meta_extension(self):
        assert job_store.job_meta_path("daily-1").name == "daily-1.meta.json"


class TestResolveJobResultFile:
    """Containment sits in the resolver itself, so no caller can end up holding
    a path that escaped the job log dir (CodeQL py/path-injection)."""

    def test_rejects_absolute_path_outside_the_job_log_dir(self):
        with pytest.raises(ValueError):
            job_store.resolve_job_result_file("/etc/passwd")

    def test_rejects_traversal_out_of_the_job_log_dir(self):
        escaping = context.RESOLVED_JOB_LOG_DIR / ".." / "escaped.result.json"
        with pytest.raises(ValueError):
            job_store.resolve_job_result_file(str(escaping))

    def test_returns_resolved_path_inside_the_job_log_dir(self):
        good = context.RESOLVED_JOB_LOG_DIR / "daily-1.result.json"
        assert job_store.resolve_job_result_file(str(good)) == good

    def test_load_result_summary_ignores_a_path_outside_the_dir(self):
        assert job_store.load_result_summary("/etc/passwd") is None

    def test_rejects_a_symlink_that_resolves_outside_the_job_log_dir(self, tmp_path):
        """The traversal tests above cover '..' in the input; a symlink planted
        inside the job log dir still names a file outside it after resolve()."""
        outside = tmp_path / "not_a_job_result.json"
        outside.write_text('{"leaked": true}')
        link = context.RESOLVED_JOB_LOG_DIR / "escape.result.json"
        link.symlink_to(outside)
        try:
            with pytest.raises(ValueError):
                job_store.resolve_job_result_file(str(link))
        finally:
            link.unlink()


class TestValidateJobResultFile:
    def test_rejects_path_outside_job_log_dir(self):
        with pytest.raises(HTTPException) as exc:
            job_store.validate_job_result_file("not-under-job-logs.json")
        assert exc.value.status_code == 400

    def test_accepts_result_json_under_dir(self):
        good = context.RESOLVED_JOB_LOG_DIR / "daily-1.result.json"
        job_store.validate_job_result_file(str(good))  # must not raise

    def test_rejects_wrong_suffix_under_dir(self):
        bad = context.RESOLVED_JOB_LOG_DIR / "daily-1.txt"
        with pytest.raises(HTTPException) as exc:
            job_store.validate_job_result_file(str(bad))
        assert exc.value.status_code == 400
