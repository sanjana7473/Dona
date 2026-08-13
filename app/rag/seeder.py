"""
seeder.py
Pre-populates the ChromaDB knowledge base with curated documentation
and known failure-fix patterns for the four failure categories described
in the paper:
  1. Dependency errors
  2. Docker build failures
  3. Test failures (flaky / assertion)
  4. IaC misconfigurations (Terraform / Kubernetes)

Call ``seed_knowledge_base()`` once at application startup; the upsert
logic in VectorStore guarantees idempotency.
"""

from app.rag.store import VectorStore

# ---------------------------------------------------------------------------
# Curated knowledge documents
# ---------------------------------------------------------------------------

_DOCUMENTS: list[tuple[str, dict]] = [

    # -----------------------------------------------------------------------
    # GitHub Actions general
    # -----------------------------------------------------------------------
    (
        """GitHub Actions Workflow Basics:
        A workflow is defined in a YAML file inside .github/workflows/.
        Each workflow contains one or more jobs that run on runners.
        Jobs run in parallel by default; use 'needs' to create dependencies.
        Steps inside a job run sequentially.
        Use 'actions/checkout@v4' to check out your repository code.
        Environment variables can be set at workflow, job, or step level.
        Secrets are accessed via ${{ secrets.SECRET_NAME }}.
        Use 'continue-on-error: true' on a step to allow the workflow to
        continue even if that step fails.
        """,
        {"source": "github_actions_docs", "category": "general"},
    ),
    (
        """GitHub Actions Troubleshooting Guide:
        Common causes of workflow failures:
        - Incorrect YAML indentation causing parse errors.
        - Missing required secrets or environment variables.
        - Runner out of disk space: use 'df -h' in a debug step.
        - Timeout: default job timeout is 6 hours; set 'timeout-minutes'.
        - Permission denied: check GITHUB_TOKEN permissions in settings.
        - Cache invalidation: increment cache key version suffix.
        Enable debug logging by setting secret ACTIONS_RUNNER_DEBUG=true.
        """,
        {"source": "github_actions_docs", "category": "general"},
    ),

    # -----------------------------------------------------------------------
    # Dependency errors
    # -----------------------------------------------------------------------
    (
        """Dependency Error Pattern – Python pip version conflict:
        Symptom: 'ERROR: Cannot install X because Y requires Z>=a.b but you have Z==c.d'
        Root cause: Two packages require incompatible versions of a shared dependency.
        Fix:
          1. Run 'pip install --upgrade pip' then retry.
          2. Pin the conflicting package to a compatible version in requirements.txt.
          3. Use a virtual environment to isolate dependencies.
          4. Use 'pip-compile' from pip-tools to resolve a consistent lock file.
          5. Check for transitive conflicts with 'pipdeptree'.
        """,
        {"source": "dependency_docs", "category": "dependency_error"},
    ),
    (
        """Dependency Error Pattern – Node.js npm/yarn resolution failure:
        Symptom: 'npm ERR! ERESOLVE unable to resolve dependency tree' or
                 'error An unexpected error occurred: "https://registry... 404"'
        Root cause: Incompatible peer dependencies or private registry unreachable.
        Fix:
          1. Delete node_modules and package-lock.json, then run 'npm install' again.
          2. Use '--legacy-peer-deps' flag as a short-term workaround.
          3. Verify .npmrc or .yarnrc for correct registry URL and auth token.
          4. Pin the offending transitive dependency using 'overrides' (npm 8+)
             or 'resolutions' (yarn).
        """,
        {"source": "dependency_docs", "category": "dependency_error"},
    ),
    (
        """Dependency Error Pattern – Missing package / ModuleNotFoundError:
        Symptom: 'ModuleNotFoundError: No module named X' or
                 'ImportError: cannot import name Y from Z'
        Root cause: Package not installed in the active environment, or installed
                    in a different Python interpreter than the one running the tests.
        Fix:
          1. Add the package to requirements.txt / pyproject.toml and reinstall.
          2. Ensure 'pip install' targets the same Python used to run scripts:
             'python -m pip install -r requirements.txt'.
          3. Check that setup.py / pyproject.toml lists the package as a dependency
             if this is a distributable project.
          4. Use 'which python' and 'which pip' to confirm they point to the same env.
        """,
        {"source": "dependency_docs", "category": "dependency_error"},
    ),

    # -----------------------------------------------------------------------
    # Docker build failures
    # -----------------------------------------------------------------------
    (
        """Docker Build Failure – Base image not found:
        Symptom: 'Error response from daemon: pull access denied' or
                 'manifest unknown: manifest unknown'
        Root cause: Image name/tag is incorrect, registry credentials missing,
                    or image has been deleted from the registry.
        Fix:
          1. Verify the image name and tag with 'docker pull <image>' locally.
          2. Log in to the registry: 'docker login <registry>' before build.
          3. Store credentials in GitHub secrets and use 'docker/login-action'.
          4. Use a specific digest (sha256:...) instead of a mutable tag like 'latest'.
          5. Mirror critical base images to your own registry to avoid rate limits.
        """,
        {"source": "docker_docs", "category": "docker_failure"},
    ),
    (
        """Docker Build Failure – Layer cache invalidation / slow builds:
        Symptom: Build takes much longer than expected; 'CACHED' steps disappear.
        Root cause: A change in an early layer (e.g., copying source code before
                    installing dependencies) invalidates all subsequent layers.
        Fix:
          1. Order Dockerfile instructions from least to most frequently changing:
             COPY requirements.txt . → RUN pip install → COPY . .
          2. Use BuildKit: 'DOCKER_BUILDKIT=1 docker build ...'
          3. Use 'docker/build-push-action' with 'cache-from' and 'cache-to' in CI.
          4. Use multi-stage builds to keep the final image lean.
        """,
        {"source": "docker_docs", "category": "docker_failure"},
    ),
    (
        """Docker Build Failure – Dockerfile syntax / RUN command error:
        Symptom: 'The command '/bin/sh -c ...' returned a non-zero code: 1'
        Root cause: A RUN command exits with a non-zero status (e.g., apt-get
                    fails because the package list is stale, or a script error).
        Fix:
          1. Add 'RUN apt-get update && apt-get install -y <package>' in one layer.
          2. Use 'set -e' at the top of multi-line RUN scripts.
          3. Use '--no-cache' on 'apt-get install' to avoid stale cache.
          4. Inspect the full build output; Docker prints the failing command.
          5. Test the RUN command interactively: 'docker run --rm -it <base> bash'.
        """,
        {"source": "docker_docs", "category": "docker_failure"},
    ),

    # -----------------------------------------------------------------------
    # Test failures
    # -----------------------------------------------------------------------
    (
        """Test Failure Pattern – Flaky / environment-dependent tests:
        Symptom: Tests pass locally but fail intermittently in CI, or pass on
                 one runner OS but fail on another.
        Root cause: Tests depend on timing, external services, file system ordering,
                    or environment variables not present in CI.
        Fix:
          1. Identify flaky tests with pytest-rerunfailures and quarantine them.
          2. Mock external HTTP calls with 'responses' or 'pytest-httpserver'.
          3. Use fixed seeds for random number generators in tests.
          4. Ensure CI environment variables match local dev (use .env.ci file).
          5. Add 'pytest-xdist' isolation: run each test in its own process.
        """,
        {"source": "test_docs", "category": "test_failure"},
    ),
    (
        """Test Failure Pattern – AssertionError / regression:
        Symptom: 'AssertionError: assert X == Y' – expected value does not match actual.
        Root cause: A code change altered the behaviour that the test was asserting.
                    Could be an intentional change (test needs updating) or a bug.
        Fix:
          1. Read the full assertion message to understand expected vs actual.
          2. Check the git diff for the failing module to identify recent changes.
          3. Run the specific test locally: 'pytest tests/test_X.py::test_func -v'.
          4. If the new behaviour is correct, update the test fixture/expected value.
          5. If it is a regression, revert or fix the offending commit.
        """,
        {"source": "test_docs", "category": "test_failure"},
    ),

    # -----------------------------------------------------------------------
    # IaC misconfigurations
    # -----------------------------------------------------------------------
    (
        """IaC Misconfiguration – Terraform invalid HCL:
        Symptom: 'Error: An argument or block definition is required' or
                 'Error: Invalid expression' during 'terraform validate' or 'plan'.
        Root cause: Syntax error in .tf file – missing quotes, wrong block type,
                    or unsupported argument for the provider version in use.
        Fix:
          1. Run 'terraform fmt -check' to detect formatting issues.
          2. Run 'terraform validate' locally before committing.
          3. Check the provider changelog for renamed/removed arguments.
          4. Use a pre-commit hook with 'terraform validate'.
          5. Pin the provider version in 'required_providers' to avoid drift.
        """,
        {"source": "iac_docs", "category": "iac_misconfiguration"},
    ),
    (
        """IaC Misconfiguration – Kubernetes manifest schema violation:
        Symptom: 'error validating data: ValidationError(Deployment.spec...)'
                 or 'unknown field "X" in io.k8s.api...'
        Root cause: The manifest uses an API field that does not exist in the
                    target cluster's Kubernetes version, or has the wrong type.
        Fix:
          1. Run 'kubectl apply --dry-run=client -f manifest.yaml' locally.
          2. Use 'kubeval' or 'kubeconform' in CI to validate manifests.
          3. Check the apiVersion – e.g., 'extensions/v1beta1' was removed in k8s 1.16.
          4. Use 'kubectl explain <resource>.<field>' to inspect valid fields.
          5. Migrate deprecated apiVersions with 'kubectl convert'.
        """,
        {"source": "iac_docs", "category": "iac_misconfiguration"},
    ),
]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def seed_knowledge_base(store: VectorStore) -> int:
    """Ingest all curated documents into *store*.  Returns number of chunks added."""
    texts = [doc for doc, _ in _DOCUMENTS]
    metas = [meta for _, meta in _DOCUMENTS]
    return store.add_documents(texts, metas, chunk=True)
