"""
scenarios.py
------------
The evaluation dataset: 15–20 handcrafted GitHub Actions failure scenarios
across the four categories defined in the paper (Section IV.E):

    1. dependency_error
    2. docker_failure
    3. test_failure
    4. iac_misconfiguration

Each scenario is constructed by deliberately injecting a single fault into
an otherwise-passing pipeline and capturing the synthetic GitHub Actions log.
For every scenario we record:

    * id                  – unique scenario identifier.
    * category            – ground-truth failure category.
    * raw_log             – the injected failure log (synthetic).
    * expected_root_cause – concise description of the true root cause.
    * remediation_keywords – tokens/phrases that must appear (case-insensitive)
                              in the agent's recommended_fix for it to be
                              considered actionable.
    * source              – origin tag (always "synthetic" for reproducibility).

These ground-truth fields are consumed by the metric pipeline in Update 5.
"""

from typing import TypedDict


class FailureScenario(TypedDict):
    id: str
    category: str
    description: str
    raw_log: str
    expected_root_cause: str
    remediation_keywords: list[str]
    source: str


# ---------------------------------------------------------------------------
# Curated scenario corpus
# ---------------------------------------------------------------------------

SCENARIOS: list[FailureScenario] = [

    # -----------------------------------------------------------------------
    # 1. DEPENDENCY ERRORS (5)
    # -----------------------------------------------------------------------
    {
        "id": "DEP-001",
        "category": "dependency_error",
        "description": "pip version conflict between urllib3 and charset-normalizer",
        "raw_log": """Run pip install -r requirements.txt
  /opt/hostedtoolcache/Python/3.11.9/x64/bin/python -m pip install -r requirements.txt
Collecting requests==2.31.0
  Using cached requests-2.31.0-py3-none-any.whl
Collecting urllib3<3,>=1.21.1
  Downloading urllib3-2.2.1-py3-none-any.whl
ERROR: Cannot install requests==2.31.0 and urllib3==1.26.16 because these package
versions have conflicting dependencies.
    The user requested urllib3==1.26.16
    requests 2.31.0 depends on urllib3<3 and >=1.21.1
##[error]The conflict is caused by: requests 2.31.0 depends on urllib3<3,>=1.21.1
Process completed with exit code 1.""",
        "expected_root_cause": "Incompatible pinned versions of urllib3 and requests: "
                                "requests 2.31.0 requires urllib3>=1.21.1,<3 but the "
                                "environment pins urllib3==1.26.16 which conflicts.",
        "remediation_keywords": ["urllib3", "requests", "pin", "requirements.txt"],
        "source": "synthetic",
    },
    {
        "id": "DEP-002",
        "category": "dependency_error",
        "description": "npm ERESOLVE peer dependency conflict",
        "raw_log": """Run npm ci
  npm ci
npm ERR! ERESOLVE unable to resolve dependency tree
npm ERR!
npm ERR! While resolving: frontend-app@1.0.0
npm ERR! Found: react@17.0.2
npm ERR! node_modules/react
npm ERR!   react@"17.0.2" from the root project
npm ERR!
npm ERR! Could not resolve dependency:
npm ERR! peer react@"^18.0.0" from react-datepicker@4.8.0
npm ERR! node_modules/react-datepicker
npm ERR!   react-datepicker@"^4.8.0" from the root project
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "react-datepicker 4.8.0 requires peer react ^18.0.0 "
                                "but the project pins react 17.0.2.",
        "remediation_keywords": ["react", "peer", "legacy-peer-deps", "resolutions"],
        "source": "synthetic",
    },
    {
        "id": "DEP-003",
        "category": "dependency_error",
        "description": "ModuleNotFoundError for a missing package",
        "raw_log": """Run pytest tests/ -v
  pytest tests/ -v
Traceback (most recent call last):
  File "/opt/hostedtoolcache/Python/3.11.9/x64/bin/pytest", line 8, in <module>
    sys.exit(console.main())
  File ".../_pytest/config/__init__.py", line 187, in main
    config = prepare()
  File "tests/test_api.py", line 3, in <module>
    import requests
ModuleNotFoundError: No module named 'requests'
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The 'requests' package is imported in tests but not "
                                "declared in requirements.txt, so it is not installed "
                                "in the CI environment.",
        "remediation_keywords": ["requests", "requirements.txt", "pip install"],
        "source": "synthetic",
    },
    {
        "id": "DEP-004",
        "category": "dependency_error",
        "description": "pip no matching distribution for a version",
        "raw_log": """Run python -m pip install fastapi==0.99.0
  python -m pip install fastapi==0.99.0
Collecting fastapi==0.99.0
  Could not find a version that satisfies the requirement fastapi==0.99.0
  (from versions: 0.1.0, 0.10.0, ..., 0.95.1, 0.96.0, 0.97.0, 0.98.0,
  0.100.0, 0.101.0)
ERROR: No matching distribution found for fastapi==0.99.0
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "fastapi 0.99.0 does not exist on PyPI — the requested "
                                "version was never published.",
        "remediation_keywords": ["fastapi", "version", "PyPI", "requirements.txt"],
        "source": "synthetic",
    },
    {
        "id": "DEP-005",
        "category": "dependency_error",
        "description": "yarn registry 404 unreachable private registry",
        "raw_log": """Run yarn install --frozen-lockfile
  yarn install --frozen-lockfile
yarn install v1.22.19
[1/4] Resolving packages...
[2/4] Fetching packages...
error An unexpected error occurred: "https://npm.internal.corp/@scope/pkg/-/pkg-1.4.2.tgz: getaddrinfo ENOTFOUND npm.internal.corp".
info If you think this is an error, please open a bug report with the information above.
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The lockfile references a private registry host "
                                "(npm.internal.corp) that is not reachable from the "
                                "GitHub Actions runner.",
        "remediation_keywords": ["registry", "yarn", ".npmrc", ".yarnrc", "auth"],
        "source": "synthetic",
    },

    # -----------------------------------------------------------------------
    # 2. DOCKER BUILD FAILURES (5)
    # -----------------------------------------------------------------------
    {
        "id": "DOC-001",
        "category": "docker_failure",
        "description": "base image pull access denied",
        "raw_log": """Run docker build -t myapp:latest .
  docker build -t myapp:latest .
Step 1/8 : FROM ghcr.io/private-org/base:1.2.3
Get https://ghcr.io/v2/private-org/base/manifests/1.2.3: unauthorized:
incorrect username or password
Error response from daemon: pull access denied for ghcr.io/private-org/base,
repository does not exist or may require 'docker login'
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The base image ghcr.io/private-org/base:1.2.3 is "
                                "private and the runner has not authenticated to "
                                "ghcr.io before the build.",
        "remediation_keywords": ["docker login", "login-action", "ghcr.io", "credentials", "secret"],
        "source": "synthetic",
    },
    {
        "id": "DOC-002",
        "category": "docker_failure",
        "description": "manifest unknown for a wrong tag",
        "raw_log": """Run docker build -t api:latest .
  docker build -t api:latest .
Step 1/6 : FROM node:18.99.0-alpine
manifest unknown: manifest unknown
##[error]The command '/bin/sh -c' exited with code 1
Error: build failed with exit code 1
Process completed with exit code 1.""",
        "expected_root_cause": "The base image tag node:18.99.0-alpine does not exist "
                                "on Docker Hub; the tag is mistyped or never published.",
        "remediation_keywords": ["node", "tag", "Dockerfile", "FROM", "digest"],
        "source": "synthetic",
    },
    {
        "id": "DOC-003",
        "category": "docker_failure",
        "description": "RUN apt-get fails because package list is stale",
        "raw_log": """Run docker build -t srv:latest .
Step 4/9 : RUN apt-get install -y curl
 ---> Running in 4f3c1a8b9d2e
Reading package lists... Done
Building dependency tree... Done
E: Unable to locate package curl
The command '/bin/sh -c apt-get install -y curl' returned a non-zero code: 100
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The Dockerfile RUN apt-get install runs without a "
                                "preceding apt-get update, so the package index is "
                                "stale and curl cannot be located.",
        "remediation_keywords": ["apt-get update", "Dockerfile", "RUN", "install"],
        "source": "synthetic",
    },
    {
        "id": "DOC-004",
        "category": "docker_failure",
        "description": "layer cache invalidation due to bad Dockerfile order",
        "raw_log": """Run docker/build-push-action@v5
  with:
    context: .
    file: ./Dockerfile
#4 [internal] load build definition from Dockerfile
#7 [base 1/5] COPY . /app
#8 [base 2/5] RUN pip install -r requirements.txt
#9 CACHED
... warning: cache miss on step 2/5; rebuilding all subsequent layers
Build time: 9m 42s (previously 1m 12s)
##[error]Cache invalidated — performance regression detected.""",
        "expected_root_cause": "COPY . /app is placed before pip install, so every "
                                "source change invalidates the dependency layer and "
                                "forces a full rebuild.",
        "remediation_keywords": ["COPY", "requirements.txt", "Dockerfile", "order", "cache"],
        "source": "synthetic",
    },
    {
        "id": "DOC-005",
        "category": "docker_failure",
        "description": "Dockerfile syntax error: unterminated RUN",
        "raw_log": """Run docker build -t web:latest .
  docker build -t web:latest .
Error: dockerfile parse error on line 12: unknown instruction "&&"
  line 12:     && apt-get install -y nginx
failed to solve: failed to process build context
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "A multi-line RUN command in the Dockerfile is broken "
                                "by an instruction boundary so the line continuation "
                                "'&&' is parsed as a top-level instruction.",
        "remediation_keywords": ["Dockerfile", "RUN", "syntax", "backslash", "line"],
        "source": "synthetic",
    },

    # -----------------------------------------------------------------------
    # 3. TEST FAILURES (4)
    # -----------------------------------------------------------------------
    {
        "id": "TST-001",
        "category": "test_failure",
        "description": "AssertionError regression in API test",
        "raw_log": """Run pytest tests/test_api.py -v
  pytest tests/test_api.py -v
tests/test_api.py::test_create_user FAILED                                  [ 12%]
tests/test_api.py::test_create_user
    def test_create_user():
        resp = client.post("/users", json=payload)
>       assert resp.status_code == 201
E       assert 404 == 201
E        +  where 404 = <Response [404]>.status_code
1 failed in 0.42s
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The /users endpoint returns 404 instead of 201, "
                                "indicating the route is missing or a recent change "
                                "altered the expected behaviour asserted by the test.",
        "remediation_keywords": ["assert", "status_code", "/users", "regression", "route"],
        "source": "synthetic",
    },
    {
        "id": "TST-002",
        "category": "test_failure",
        "description": "flaky timing-dependent test",
        "raw_log": """Run pytest tests/test_cache.py -v
tests/test_cache.py::test_ttl_expiry FAILED
>   assert cache.get("k") is None
E   assert 'v' is None
E   Cache key 'k' expected to expire after 1s but still present after 1s on CI runner.
1 failed in 1.13s
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The test relies on a 1s wall-clock TTL but CI runners "
                                "have variable scheduling, so the cache has not "
                                "expired yet when the assertion runs — a timing-"
                                "dependent flaky test.",
        "remediation_keywords": ["flaky", "timing", "ttl", "mock", "sleep"],
        "source": "synthetic",
    },
    {
        "id": "TST-003",
        "category": "test_failure",
        "description": "environment-dependent test missing env var",
        "raw_log": """Run pytest tests/test_config.py -v
tests/test_config.py::test_loads_api_key ERROR
E   KeyError: 'API_KEY'
E   test expected os.environ['API_KEY'] to be set; the variable is missing in CI.
1 error in 0.08s
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The test reads os.environ['API_KEY'] directly but the "
                                "variable is not configured in the GitHub Actions "
                                "environment, causing a KeyError.",
        "remediation_keywords": ["API_KEY", "environ", "secret", "env", "mock"],
        "source": "synthetic",
    },
    {
        "id": "TST-004",
        "category": "test_failure",
        "description": "assertion failure due to floating point drift",
        "raw_log": """Run pytest tests/test_pricing.py -v
tests/test_pricing.py::test_rounding FAILED
>   assert total == 19.99
E   assert 19.989999999999998 == 19.99
E   Floating-point drift between local and CI Python builds.
1 failed in 0.21s
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "Floating-point drift produces 19.989999999999998 "
                                "instead of 19.99; the test uses exact equality "
                                "instead of an approximate comparison.",
        "remediation_keywords": ["float", "round", "assert", "pytest.approx", "decimal"],
        "source": "synthetic",
    },

    # -----------------------------------------------------------------------
    # 4. IaC MISCONFIGURATIONS (4)
    # -----------------------------------------------------------------------
    {
        "id": "IAC-001",
        "category": "iac_misconfiguration",
        "description": "Terraform invalid HCL block",
        "raw_log": """Run terraform validate
  terraform validate
Error: An argument or block definition is required here
  on main.tf line 17, in resource "aws_s3_bucket" "data":
  17:   tags = {
  18:     Name = "data-bucket"
  19:   }

An argument or block definition is required on line 17. Did you mean to
define a "tags" block? If so, use double-quoted block syntax.
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "main.tf line 17 has invalid HCL: the tags block is "
                                "missing the equal-sign assignment context expected "
                                "by the Terraform parser.",
        "remediation_keywords": ["terraform", "validate", "main.tf", "HCL", "fmt"],
        "source": "synthetic",
    },
    {
        "id": "IAC-002",
        "category": "iac_misconfiguration",
        "description": "Kubernetes manifest unknown field schema violation",
        "raw_log": """Run kubectl apply --dry-run=client -f deploy.yaml
  kubectl apply --dry-run=client -f deploy.yaml
error: error validating data: ValidationError(Deployment.spec.template.spec.containers[0]):
unknown field "pullSecrets" in io.k8s.api.core.v1.Container
If you choose to ignore these errors, turn validation off with --validate=false.
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The manifest uses the non-existent field "
                                "pullSecrets on a container; the correct field is "
                                "imagePullSecrets at the pod spec level.",
        "remediation_keywords": ["imagePullSecrets", "manifest", "kubectl", "validate", "schema"],
        "source": "synthetic",
    },
    {
        "id": "IAC-003",
        "category": "iac_misconfiguration",
        "description": "Terraform unsupported provider argument",
        "raw_log": """Run terraform plan
  terraform plan
Error: Unsupported argument name
  on providers.tf line 8, in provider "aws":
   8:   default_region = "eu-west-1"

The provider "aws" does not support an argument named "default_region". Did
you mean "region"?
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The aws provider block uses default_region which is "
                                "not a valid argument; the correct argument is region.",
        "remediation_keywords": ["region", "provider", "aws", "terraform", "default_region"],
        "source": "synthetic",
    },
    {
        "id": "IAC-004",
        "category": "iac_misconfiguration",
        "description": "Kubernetes deprecated apiVersion removed in cluster version",
        "raw_log": """Run kubectl apply -f ingress.yaml
  kubectl apply -f ingress.yaml
error: resource mapping not found for name "main-ingress" namespace "default"
from "ingress.yaml": no matches for kind "Ingress" in version "extensions/v1beta1"
ensure CRDs are installed first. The extensions/v1beta1 API was removed in
Kubernetes 1.22; use networking.k8s.io/v1.
##[error]Process completed with exit code 1.""",
        "expected_root_cause": "The Ingress manifest uses apiVersion extensions/v1beta1 "
                                "which was removed in Kubernetes 1.22; it must be "
                                "migrated to networking.k8s.io/v1.",
        "remediation_keywords": ["apiVersion", "networking.k8s.io/v1", "ingress", "convert", "extensions"],
        "source": "synthetic",
    },
]


# ---------------------------------------------------------------------------
# Convenience accessors
# ---------------------------------------------------------------------------

def list_scenarios() -> list[FailureScenario]:
    """Return all injected failure scenarios."""
    return SCENARIOS


def scenarios_by_category() -> dict[str, list[FailureScenario]]:
    """Group scenarios by their ground-truth category."""
    grouped: dict[str, list[FailureScenario]] = {}
    for s in SCENARIOS:
        grouped.setdefault(s["category"], []).append(s)
    return grouped


def scenario_ids() -> list[str]:
    return [s["id"] for s in SCENARIOS]


def get_scenario(scenario_id: str) -> FailureScenario | None:
    for s in SCENARIOS:
        if s["id"] == scenario_id:
            return s
    return None
