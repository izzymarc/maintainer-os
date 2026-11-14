import subprocess
import sys
from pathlib import Path
from uuid import UUID

from .domain import WorkflowRun, WorkflowStatus, WorkerResult


class WorkerError(ValueError):
    """Raised when a workflow cannot be safely executed."""


class LocalWorker:
    """Execute fixed, read-only analysis profiles in a local workspace.

    This is a development runner, not a container sandbox. Production execution
    must add OS/container isolation before untrusted repository code is run.
    """

    COMMANDS: dict[str, tuple[str, ...]] = {
        "review": (sys.executable, "-m", "compileall", "-q", "."),
        "test_generation": (sys.executable, "-m", "pytest", "-q"),
        "issue_triage": (sys.executable, "-m", "compileall", "-q", "."),
    }

    def __init__(self, *, timeout_seconds: int = 30, max_output_bytes: int = 32_000) -> None:
        if timeout_seconds < 1:
            raise ValueError("timeout_seconds must be positive")
        if max_output_bytes < 1:
            raise ValueError("max_output_bytes must be positive")
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes

    def execute(self, run: WorkflowRun, workspace: Path) -> WorkerResult:
        if run.status is not WorkflowStatus.CLAIMED:
            raise WorkerError("workflow run must be claimed before execution")
        workspace = workspace.resolve()
        if not workspace.is_dir():
            raise WorkerError("workspace must be an existing directory")
        command = self.COMMANDS.get(run.workflow_type.value)
        if command is None:
            raise WorkerError("workflow type has no safe execution profile")

        try:
            completed = subprocess.run(
                command,
                cwd=workspace,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                shell=False,
                check=False,
            )
            stdout, stdout_truncated = self._cap(completed.stdout)
            stderr, stderr_truncated = self._cap(completed.stderr)
            return WorkerResult(
                run_id=run.id,
                status="succeeded" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                stdout=stdout,
                stderr=stderr,
                output_truncated=stdout_truncated or stderr_truncated,
            )
        except subprocess.TimeoutExpired as error:
            stdout = self._decode(error.stdout)
            stderr = self._decode(error.stderr)
            stdout, stdout_truncated = self._cap(stdout)
            stderr, stderr_truncated = self._cap(stderr)
            return WorkerResult(
                run_id=run.id,
                status="timed_out",
                stdout=stdout,
                stderr=stderr,
                timed_out=True,
                output_truncated=stdout_truncated or stderr_truncated,
            )

    def _cap(self, value: str | None) -> tuple[str, bool]:
        value = value or ""
        encoded = value.encode("utf-8", errors="replace")
        if len(encoded) <= self.max_output_bytes:
            return value, False
        return encoded[: self.max_output_bytes].decode("utf-8", errors="ignore"), True

    @staticmethod
    def _decode(value: str | bytes | None) -> str:
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return value or ""
