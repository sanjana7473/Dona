# AI-Powered CI/CD Failure Diagnosis System

A college-project prototype that diagnoses CI/CD pipeline failures using Flask, log parsing, Retrieval-Augmented Generation (RAG), ChromaDB, Sentence Transformers, and a LangGraph/OpenAI agent.

The project supports three log sources:

1. Manual upload or pasted logs.
2. Locally generated synthetic logs created by Python, with optional AI wording variation.
3. Realistic synthetic failures executed on GitHub-hosted runners through GitHub Actions.

The project does not deploy applications, modify infrastructure, or apply AI-generated fixes to production systems.

## Project Objective

The system accepts a raw CI/CD failure log and returns a structured diagnosis containing:

- Failure category.
- Root-cause explanation.
- Confidence score.
- Recommended remediation steps.
- Retrieved supporting references.

Supported failure categories:

- `dependency_error`
- `docker_failure`
- `test_failure`
- `iac_misconfiguration`
- `unknown`

The academic evaluation compares three diagnosis conditions:

1. **LLM only** — no retrieval context.
2. **RAG with documentation** — curated documentation only.
3. **Full RAG** — documentation plus historical failure-fix pairs.

## System Architecture

```text
Raw CI/CD log
      |
      v
Flask web UI or REST API
      |
      v
Request validation and log parser
      |
      +-----------------------------+
      |                             |
      v                             v
ChromaDB RAG retrieval       LangGraph ReAct agent
      |                             |
      +-------------+---------------+
                    v
          Validated diagnosis JSON
                    |
          +---------+----------+
          |                    |
          v                    v
   SQLite history       ChromaDB feedback pair
```

### Main modules

| Path | Responsibility |
| --- | --- |
| `run.py` | Starts the Flask server |
| `config.py` | Loads environment configuration |
| `app/__init__.py` | Flask application factory and dependency injection |
| `app/routes/main.py` | Web page and health endpoint |
| `app/routes/diagnosis.py` | Diagnosis, history, and knowledge-base APIs |
| `app/routes/generation.py` | Local generation and GitHub Actions generation APIs |
| `app/utils/diagnosis.py` | Shared diagnosis and persistence pipeline |
| `app/utils/log_parser.py` | Initial log parsing and category detection |
| `app/utils/validation.py` | Pydantic diagnosis-response validation |
| `app/utils/history.py` | SQLite storage, hashing, pagination, and filters |
| `app/rag/store.py` | ChromaDB, embeddings, token chunking, and retrieval |
| `app/rag/seeder.py` | Curated knowledge-base content |
| `app/agent/react_agent.py` | LangGraph ReAct diagnosis agent |
| `app/agent/verifier.py` | Independent AI diagnosis reviewer |
| `app/generation/local.py` | Safe deterministic Python log generator and optional AI variation |
| `app/integrations/github.py` | Server-side workflow dispatch, polling, and artifact retrieval |
| `evaluation/scenarios.py` | Built-in synthetic scenario corpus |
| `evaluation/github_logs.py` | Loads downloaded GitHub Actions artifacts |
| `evaluation/conditions.py` | LLM-only, documentation-RAG, and full-RAG modes |
| `evaluation/runner.py` | Runs scenarios and saves raw results |
| `evaluation/metrics.py` | Computes evaluation metrics |
| `.github/workflows/ci.yml` | Normal project CI test workflow |
| `.github/workflows/generate-failure-logs.yml` | Realistic failure-log generator |

## Requirements

- Python 3.10 or newer.
- An OpenAI API key for live diagnosis requests.
- Internet access when downloading Python packages and the embedding model.
- A GitHub repository and server-side token if using automatic GitHub Actions generation.
- Optional: GitHub CLI (`gh`) for manually downloading workflow artifacts.

The automated tests mock the LLM and vector store, so an OpenAI key is not required to run tests.

## Local Installation

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate       # macOS/Linux
# venv\\Scripts\\activate    # Windows
```

Install dependencies:

```bash
pip install -r requirements.txt
```

The repository includes a local `.env` template. Replace the placeholder API key before using live diagnosis:

```env
OPENAI_API_KEY=sk-your-openai-api-key-here
SECRET_KEY=change-this-development-secret
FLASK_ENV=development
FLASK_DEBUG=1
CHROMA_PERSIST_DIR=./chromadb_store
DIAGNOSIS_HISTORY_PATH=./diagnosis_history.db
TOP_K_RESULTS=5
CHUNK_SIZE=512
CHUNK_OVERLAP=64
MAX_LOG_CHARS=100000
HISTORY_PAGE_SIZE=20
MAX_HISTORY_PAGE_SIZE=100
RETRIEVAL_MAX_LOG_CHARS=2000
LLM_MODEL=gpt-5.4-mini-2026-03-17
LLM_TEMPERATURE=0

# Optional GitHub Actions integration
GITHUB_TOKEN=github-token-for-test-repository
GITHUB_REPOSITORY=owner/repository
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
GITHUB_DEFAULT_REF=main
GITHUB_API_ROOT=https://api.github.com
GITHUB_HTTP_TIMEOUT=20
GITHUB_POLL_INTERVAL=3
GITHUB_RUN_TIMEOUT=300
```

Never commit a real `.env` file or API key. The project `.gitignore` excludes local secrets, databases, ChromaDB data, and generated evaluation output.

## Run the Flask Application

Start the local development server:

```bash
python run.py
```

Open:

```text
http://localhost:5000
```

The frontend contains three sections:

1. Upload and analyze an existing log.
2. Generate a bounded batch of local Python/optional-AI logs sequentially, with progress and Stop controls.
3. Dispatch GitHub Actions, fetch its artifact, and diagnose the result.

Paste a failure log into the first section or use one of the generation buttons.

## API Reference

### Health check

```http
GET /health
```

Example response:

```json
{
  "status": "ok",
  "message": "CI/CD Diagnosis API is running"
}
```

### Diagnose a failure log

```http
POST /api/diagnose
Content-Type: application/json
```

Request:

```json
{
  "log": "ModuleNotFoundError: No module named requests"
}
```

Response:

```json
{
  "failure_category": "dependency_error",
  "root_cause": "The requests package is not available in the CI environment.",
  "confidence_score": 0.87,
  "recommended_fix": "Add requests to requirements.txt and install it before running tests.",
  "retrieved_references": ["dependency_docs"],
  "diagnosis_mode": "rag_full"
}
```

The endpoint returns:

- `400` for invalid, missing, empty, or non-string logs.
- `413` when the log exceeds `MAX_LOG_CHARS`.
- `502` when the diagnosis agent fails or returns invalid output.
- `500` when diagnosis history cannot be saved.

### Verify a diagnosis with AI

```http
POST /api/verify
Content-Type: application/json
```

Request:

```json
{
  "log": "ModuleNotFoundError: No module named requests",
  "diagnosis": {
    "failure_category": "dependency_error",
    "root_cause": "The requests package is missing.",
    "confidence_score": 0.8,
    "recommended_fix": "Install requests in the CI dependency step.",
    "retrieved_references": []
  }
}
```

The independent AI reviewer returns `is_correct`, a confidence score, an explanation, evidence from the log, and corrected fields when the proposed diagnosis is wrong. It does not receive hidden ground-truth metadata. Every manual, local, and GitHub result card can use the same verification button.

### Generate a local synthetic log

```http
POST /api/generate/local
Content-Type: application/json
```

Request:

```json
{
  "category": "random",
  "seed": 42,
  "use_ai_variation": false
}
```

The response contains `source`, `log`, evaluation-only `ground_truth`, and `diagnosis`. Ground truth is created before diagnosis and is not sent to the agent. The frontend calls this endpoint sequentially for the selected batch size (1–20 logs), placing each diagnosis in its own result card with its own **Verify with AI** button. The user can set the interval between requests and stop the batch before it finishes.

Supported categories are `random`, `dependency_error`, `docker_failure`, `test_failure`, and `iac_misconfiguration`.

### Generate a log with GitHub Actions

```http
POST /api/github/generate
Content-Type: application/json
```

Request:

```json
{
  "category": "dependency_error",
  "ref": "main"
}
```

The endpoint returns a job ID immediately. Poll it with:

```http
GET /api/github/generate/<job_id>
```

The status changes through dispatch, run, artifact-download, and diagnosis states. On completion, the response includes the fetched log, ground truth metadata, and diagnosis. GitHub credentials remain on the Flask server.

### Diagnosis history

```http
GET /api/history
```

Optional query parameters:

```text
page=1
page_size=20
category=dependency_error
from=2026-01-01
to=2026-12-31
```

Example:

```text
/api/history?page=1&page_size=10&category=docker_failure
```

Response:

```json
{
  "diagnoses": [],
  "page": 1,
  "page_size": 10,
  "total": 0,
  "pages": 0
}
```

Each history entry contains a SHA-256 hash of the original input log, timestamp, parser category hint, and full diagnosis response.

### Knowledge-base statistics

```http
GET /api/knowledge-base/stats
```

Example response:

```json
{
  "chunk_count": 19
}
```

## Diagnosis Processing Flow

All three sources use the same processing pipeline:

```text
manual/local/GitHub log
        -> validation
        -> parser
        -> RAG retrieval
        -> LangGraph/OpenAI agent
        -> Pydantic validation
        -> SQLite history and feedback collection
        -> frontend result
```

For local generation, Python selects a known category and fills a safe template. Optional AI variation can rewrite the wording while preserving the technical facts. For GitHub generation, Flask dispatches a workflow, polls the run, downloads the artifact, and then sends only the log—not `ground_truth.json`—to the diagnosis agent. After a diagnosis is displayed, **Verify with AI** sends the log and proposed diagnosis to an independent reviewer model.

## Knowledge Base and RAG

The initial knowledge base is seeded from `app/rag/seeder.py` and covers:

- GitHub Actions workflow troubleshooting.
- Python and Node.js dependency failures.
- Docker image and Dockerfile failures.
- Flaky tests and assertion regressions.
- Terraform and Kubernetes configuration failures.

Documents are split into token-based chunks using `CHUNK_SIZE` and `CHUNK_OVERLAP`, embedded with `all-MiniLM-L6-v2`, and persisted to ChromaDB.

- `rag_docs` excludes records tagged as `feedback_loop`.
- `rag_full` can retrieve curated documents and historical failure-fix pairs.
- Actual ranked retrieval results are saved by the evaluation runner for NDCG calculation.

## GitHub Actions Realistic Log Generation

The workflow below creates realistic but safe failure logs on GitHub-hosted Ubuntu runners:

```text
.github/workflows/generate-failure-logs.yml
```

It is manually triggered and runs eight matrix scenarios:

| Scenario type | Examples |
| --- | --- |
| Dependency | pip resolution conflict, missing Python package |
| Docker | unavailable image tag, Dockerfile syntax error |
| Test | assertion regression, missing CI environment variable |
| IaC | invalid Terraform HCL, invalid Kubernetes manifest field |

Each matrix job:

1. Creates an intentionally invalid local fixture.
2. Runs the failing command on a real GitHub-hosted runner.
3. Captures stdout and stderr into a `.log` file using `tee`.
4. Adds scenario metadata and the exit code.
5. Uploads the log and `ground_truth.json` as an artifact.

The workflow uses `workflow_dispatch`, requests only `contents: read`, and does not use production credentials, deployments, or external infrastructure changes.

### Run the workflow from GitHub

1. Push the repository to GitHub.
2. Open the repository's **Actions** tab.
3. Select **Generate Realistic CI/CD Failure Logs**.
4. Select **Run workflow**.
5. Wait for all matrix jobs to finish.
6. Open a completed job and download an artifact such as `failure-log-DEP-GHA-001`.

### Download artifacts with GitHub CLI

Install and authenticate the GitHub CLI, then find the workflow run ID:

```bash
gh run list
```

Download all generated artifacts:

```bash
gh run download RUN_ID --dir github_artifacts
```

A downloaded artifact contains:

```text
github_artifacts/
└── failure-log-DEP-GHA-001/
    ├── failure-DEP-GHA-001.log
    └── ground_truth.json
```

### Evaluate GitHub-generated logs

The artifact loader converts the downloaded log and metadata into evaluation scenarios:

```bash
python -m evaluation.runner \\
  --github-artifacts github_artifacts \\
  --out evaluation_results/github_actions
```

This runs the downloaded logs through the same three diagnosis conditions and writes:

```text
evaluation_results/github_actions/raw_results.json
```

The frontend-triggered GitHub flow automatically dispatches the workflow, downloads the matching artifact, and sends the log to `/api/diagnose`'s shared backend pipeline. Manual artifact download remains available for evaluation and debugging.

### Submit a generated log to the API

With `jq` installed:

```bash
jq -Rs '{log: .}' github_artifacts/failure-log-DEP-GHA-001/failure-DEP-GHA-001.log \
  | curl -X POST http://localhost:5000/api/diagnose \
      -H "Content-Type: application/json" \
      --data-binary @-
```

## Built-in Evaluation Corpus

The repository also contains 18 reproducible synthetic scenarios in `evaluation/scenarios.py`:

- 5 dependency failures.
- 5 Docker failures.
- 4 test failures.
- 4 IaC failures.

Each scenario includes:

- Scenario ID.
- Ground-truth category.
- Raw failure log.
- Expected root cause.
- Remediation keywords.
- Source marker.

The built-in corpus is useful when GitHub Actions is unavailable or when a deterministic baseline is required. GitHub-generated artifacts provide more realistic runner output, while the built-in corpus provides broader coverage for the academic experiment.

## Run the Evaluation

Run the built-in 18-scenario evaluation:

```bash
python -m evaluation.runner
```

Run selected conditions:

```bash
python -m evaluation.runner \\
  --conditions llm_only,rag_docs
```

Run the evaluation using downloaded GitHub Actions artifacts:

```bash
python -m evaluation.runner \\
  --github-artifacts github_artifacts \\
  --out evaluation_results/github_actions
```

The runner writes `raw_results.json` incrementally so partially completed runs remain inspectable.

## Compute Metrics

```bash
python -m evaluation.metrics \\
  --input evaluation_results/raw_results.json \\
  --output evaluation_results/metrics.json \\
  --print
```

The report includes:

- Diagnostic category accuracy.
- Remediation actionability using keyword coverage.
- Mean and standard deviation of diagnosis time.
- Mean confidence score.
- Per-category accuracy and actionability.
- NDCG@5 retrieval quality.
- Confusion matrices.

## Testing

Run the complete test suite:

```bash
pytest -q
```

The tests cover:

- Flask health and diagnosis routes.
- Request validation and response structure.
- AI verification endpoint and structured reviewer output.
- SQLite history, hashing, pagination, and filters.
- Log parser categories.
- Built-in scenario integrity.
- GitHub artifact loading.
- Evaluation condition validation.
- Raw evaluation result generation.
- Metric calculation and serialization.

The test suite covers the original application plus local generation, source tracking, GitHub route validation, and artifact parsing. Run `pytest -q` to see the current count.

## Academic Evaluation Procedure

For each scenario:

1. Run the `llm_only` condition.
2. Run the `rag_docs` condition.
3. Run the `rag_full` condition.
4. Store the diagnosis with the ground truth.
5. Compare predicted and expected categories.
6. Measure remediation keyword coverage.
7. Record diagnosis time and confidence.
8. Calculate NDCG from ranked retrieval results.
9. Generate per-category tables and confusion matrices.
10. Compare the three conditions.

The research hypothesis is that documentation and historical failure retrieval improve diagnosis accuracy and remediation quality compared with the LLM-only baseline.

## Limitations

- The GitHub Actions workflow generates controlled synthetic failures, not production incidents.
- The local generator creates template-based failures; AI variation is optional and non-deterministic.
- GitHub-triggered job state is kept in memory, so restarting Flask loses active job status.
- GitHub API credentials and repository configuration are required for the third section.
- The system does not apply recommended fixes automatically.
- Diagnosis quality depends on the selected language model and API availability.
- Synthetic scenarios do not represent every real-world CI/CD failure.
- NDCG relevance labels are based on source categories rather than human relevance judgments.

## Project Documents

- `Dona_Ireland_Proposal_Updated.pdf` — research proposal and methodology.
- `Implementation_Guide.docx` — implementation requirements.
- `remaining steps.md` — implementation status and optional enhancements.
- `working procedure and task.md` — task and end-to-end workflow explanation.
- `github setup.md` — step-by-step GitHub Actions token and repository setup.
