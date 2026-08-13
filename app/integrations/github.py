"""Server-side GitHub Actions workflow integration.

The integration deliberately uses Python's standard library so the project
needs no additional HTTP dependency. GitHub credentials remain in Flask
configuration and are never exposed to the browser.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.generation.local import CATEGORIES
from app.utils.diagnosis import diagnose_and_persist

logger = logging.getLogger(__name__)

_CATEGORY_PREFIXES = {
    "dependency_error": "DEP-GHA-",
    "docker_failure": "DOC-GHA-",
    "test_failure": "TST-GHA-",
    "iac_misconfiguration": "IAC-GHA-",
}


class GitHubConfigurationError(RuntimeError):
    """GitHub integration is not configured."""


class GitHubAPIError(RuntimeError):
    """GitHub returned an error or an invalid response."""


class GitHubArtifactError(RuntimeError):
    """A generated workflow artifact could not be read safely."""


class GitHubAPIClient:
    """Small GitHub REST client for workflow dispatch and artifact retrieval."""

    API_ROOT = "https://api.github.com"

    def __init__(
        self,
        token: str | None,
        repository: str | None,
        workflow_id: str | None,
        *,
        api_root: str | None = None,
        timeout: float = 20.0,
    ):
        self.token = token
        self.repository = repository
        self.workflow_id = workflow_id
        self.api_root = (api_root or self.API_ROOT).rstrip("/")
        self.timeout = timeout

    @property
    def configured(self) -> bool:
        return bool(self.token and self.repository and self.workflow_id)

    def _require_configured(self) -> None:
        if not self.configured:
            raise GitHubConfigurationError(
                "GitHub integration requires GITHUB_TOKEN, GITHUB_REPOSITORY, "
                "and GITHUB_WORKFLOW_ID"
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        accept: str = "application/vnd.github+json",
    ) -> tuple[int, bytes, dict[str, str]]:
        self._require_configured()
        url = f"{self.api_root}{path}"
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = Request(
            url,
            data=body,
            method=method,
            headers={
                "Accept": accept,
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "cicd-failure-diagnosis-college-project",
                **({"Content-Type": "application/json"} if body else {}),
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                return response.status, response.read(), dict(response.headers.items())
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:500]
            raise GitHubAPIError(f"GitHub API {exc.code} for {method} {path}: {detail}") from exc
        except URLError as exc:
            raise GitHubAPIError(f"Could not reach GitHub: {exc.reason}") from exc

    def dispatch_workflow(self, *, category: str, ref: str) -> datetime:
        """Dispatch the configured workflow and return the dispatch timestamp."""
        if category not in CATEGORIES:
            raise ValueError(f"Unsupported GitHub category: {category}")
        status, _, _ = self._request(
            "POST",
            f"/repos/{self.repository}/actions/workflows/{self.workflow_id}/dispatches",
            payload={"ref": ref, "inputs": {"category": category}},
        )
        if status not in (200, 201, 204):
            raise GitHubAPIError(f"Unexpected workflow-dispatch status: {status}")
        return datetime.now(timezone.utc)

    def list_runs(self, *, per_page: int = 20) -> list[dict[str, Any]]:
        query = urlencode({"event": "workflow_dispatch", "per_page": per_page})
        _, body, _ = self._request(
            "GET",
            f"/repos/{self.repository}/actions/workflows/{self.workflow_id}/runs?{query}",
        )
        payload = json.loads(body or b"{}")
        return list(payload.get("workflow_runs", []))

    def get_run(self, run_id: int) -> dict[str, Any]:
        _, body, _ = self._request(
            "GET", f"/repos/{self.repository}/actions/runs/{run_id}"
        )
        return json.loads(body)

    def list_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        _, body, _ = self._request(
            "GET", f"/repos/{self.repository}/actions/runs/{run_id}/artifacts"
        )
        return list(json.loads(body or b"{}").get("artifacts", []))

    def download_artifact(self, artifact_id: int) -> bytes:
        _, body, _ = self._request(
            "GET",
            f"/repos/{self.repository}/actions/artifacts/{artifact_id}/zip",
            accept="application/vnd.github+json",
        )
        return body


@dataclass
class GitHubJob:
    """In-memory state for one frontend-triggered GitHub generation."""

    job_id: str
    category: str
    ref: str
    status: str = "queued"
    github_run_id: int | None = None
    run_conclusion: str | None = None
    artifact_name: str | None = None
    raw_log: str | None = None
    ground_truth: dict[str, Any] | None = None
    diagnosis: dict[str, Any] | None = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "job_id": self.job_id,
            "category": self.category,
            "ref": self.ref,
            "status": self.status,
            "github_run_id": self.github_run_id,
            "run_conclusion": self.run_conclusion,
            "artifact_name": self.artifact_name,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.raw_log is not None:
            payload.update(
                {
                    "source": "github_actions",
                    "log": self.raw_log,
                    "ground_truth": self.ground_truth,
                    "diagnosis": self.diagnosis,
                }
            )
        if self.error:
            payload["error"] = self.error
        return payload


class GitHubGenerationService:
    """Dispatch and process GitHub Actions generated-log jobs."""

    def __init__(self, app: Any, client: GitHubAPIClient | None = None):
        self.app = app
        self.client = client or GitHubAPIClient(
            app.config.get("GITHUB_TOKEN"),
            app.config.get("GITHUB_REPOSITORY"),
            app.config.get("GITHUB_WORKFLOW_ID"),
            api_root=app.config.get("GITHUB_API_ROOT"),
            timeout=float(app.config.get("GITHUB_HTTP_TIMEOUT", 20)),
        )
        self.poll_interval = float(app.config.get("GITHUB_POLL_INTERVAL", 3))
        self.timeout = float(app.config.get("GITHUB_RUN_TIMEOUT", 300))
        self._jobs: dict[str, GitHubJob] = {}
        self._lock = threading.RLock()

    def start(self, *, category: str, ref: str | None = None) -> dict[str, Any]:
        if category not in CATEGORIES:
            raise ValueError(f"Unsupported category {category!r}; choose one of {CATEGORIES}")
        selected_ref = ref or self.app.config.get("GITHUB_DEFAULT_REF", "main")
        job = GitHubJob(job_id=f"gha-{uuid.uuid4().hex[:12]}", category=category, ref=selected_ref)
        self._save(job)

        try:
            job.status = "dispatched"
            self._touch(job)
            dispatch_time = self.client.dispatch_workflow(category=category, ref=selected_ref)
        except (GitHubConfigurationError, GitHubAPIError, ValueError) as exc:
            job.status = "dispatch_failed"
            job.error = str(exc)
            self._touch(job)
            return job.as_dict()

        worker = threading.Thread(
            target=self._run_job,
            args=(job.job_id, dispatch_time),
            name=f"github-generation-{job.job_id}",
            daemon=True,
        )
        worker.start()
        return job.as_dict()

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.as_dict() if job else None

    def _save(self, job: GitHubJob) -> None:
        with self._lock:
            self._jobs[job.job_id] = job

    def _touch(self, job: GitHubJob) -> None:
        job.updated_at = datetime.now(timezone.utc).isoformat()
        self._save(job)

    def _update(self, job_id: str, **changes: Any) -> GitHubJob:
        with self._lock:
            job = self._jobs[job_id]
            for key, value in changes.items():
                setattr(job, key, value)
            self._touch(job)
            return job

    def _run_job(self, job_id: str, dispatch_time: datetime) -> None:
        try:
            job = self._jobs[job_id]
            run = self._wait_for_run(job.category, job.ref, dispatch_time)
            self._update(job_id, status="running", github_run_id=int(run["id"]))
            completed_run = self._wait_for_completion(int(run["id"]))
            artifacts = self.client.list_artifacts(int(run["id"]))
            artifact = self._select_artifact(artifacts, job.category)
            self._update(
                job_id,
                status="artifact_downloading",
                run_conclusion=completed_run.get("conclusion"),
                artifact_name=artifact.get("name"),
            )
            raw_log, ground_truth = self._extract_artifact(
                self.client.download_artifact(int(artifact["id"]))
            )
            self._update(job_id, status="diagnosing", raw_log=raw_log, ground_truth=ground_truth)
            with self.app.app_context():
                diagnosis = diagnose_and_persist(
                    self.app,
                    raw_log,
                    source="github_actions",
                )
            self._update(job_id, status="completed", diagnosis=diagnosis)
        except Exception as exc:
            logger.exception("GitHub generation job %s failed", job_id)
            self._update(job_id, status="failed", error=str(exc))

    def _wait_for_run(self, category: str, ref: str, dispatch_time: datetime) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        lower_bound = dispatch_time - timedelta(seconds=30)
        while time.monotonic() < deadline:
            runs = self.client.list_runs()
            candidates = []
            for run in runs:
                created = self._parse_timestamp(run.get("created_at"))
                if created >= lower_bound and run.get("head_branch") in (None, ref):
                    candidates.append(run)
            if candidates:
                return max(candidates, key=lambda item: item.get("id", 0))
            time.sleep(self.poll_interval)
        raise GitHubAPIError("Timed out waiting for the dispatched GitHub workflow run")

    def _wait_for_completion(self, run_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while time.monotonic() < deadline:
            run = self.client.get_run(run_id)
            if run.get("status") == "completed":
                return run
            time.sleep(self.poll_interval)
        raise GitHubAPIError("Timed out waiting for the GitHub workflow to complete")

    @staticmethod
    def _select_artifact(artifacts: list[dict[str, Any]], category: str) -> dict[str, Any]:
        prefix = _CATEGORY_PREFIXES[category]
        matching = [
            artifact for artifact in artifacts
            if str(artifact.get("name", "")).startswith("failure-log-")
            and prefix in str(artifact.get("name", ""))
        ]
        if not matching:
            raise GitHubArtifactError(
                f"No failure-log artifact found for category {category}; "
                f"available={[artifact.get('name') for artifact in artifacts]}"
            )
        return matching[0]

    @staticmethod
    def _extract_artifact(payload: bytes) -> tuple[str, dict[str, Any] | None]:
        try:
            archive = zipfile.ZipFile(io.BytesIO(payload))
        except zipfile.BadZipFile as exc:
            raise GitHubArtifactError("GitHub returned an invalid artifact ZIP") from exc

        log_names = [name for name in archive.namelist() if name.endswith(".log")]
        if len(log_names) != 1:
            raise GitHubArtifactError(f"Expected one .log file in artifact, found {log_names}")
        raw_log = archive.read(log_names[0]).decode("utf-8", errors="replace")
        if not raw_log.strip():
            raise GitHubArtifactError("Downloaded GitHub failure log is empty")

        ground_truth = None
        metadata_names = [name for name in archive.namelist() if name.endswith("ground_truth.json")]
        if metadata_names:
            try:
                ground_truth = json.loads(archive.read(metadata_names[0]).decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as exc:
                raise GitHubArtifactError("Artifact ground_truth.json is invalid") from exc
        return raw_log, ground_truth

    @staticmethod
    def _parse_timestamp(value: str | None) -> datetime:
        if not value:
            return datetime.min.replace(tzinfo=timezone.utc)
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return datetime.min.replace(tzinfo=timezone.utc)
