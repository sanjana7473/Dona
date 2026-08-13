# CI/CD Failure Diagnosis System

## 1. Project Task

The task is to develop and validate an AI-powered system that automatically diagnoses failures in CI/CD pipelines, especially GitHub Actions workflows.

The system must accept a raw pipeline failure log and produce a structured diagnosis containing:

- Failure category.
- Root-cause explanation.
- Confidence score.
- Recommended remediation steps.
- Retrieved reference documents or historical incidents.

The system combines:

- Flask for the web application and REST API.
- Regex and structured parsing for initial log analysis.
- Sentence Transformers for text embeddings.
- ChromaDB for persistent vector storage.
- Retrieval-Augmented Generation (RAG) for contextual evidence.
- LangGraph/LangChain for agent orchestration.
- An OpenAI language model for reasoning and response generation.

The four target failure categories are:

1. Dependency errors.
2. Docker build failures.
3. Test failures.
4. Infrastructure-as-Code misconfigurations.

## 2. Expected Three-Section User Workflow

The frontend provides three ways to obtain a failure log:

1. **Manual upload:** the user uploads or pastes an existing log and selects Analyze.
2. **Local generation:** Python selects a safe category template, creates a random synthetic log, optionally varies its wording with AI, and immediately diagnoses it.
3. **GitHub Actions generation:** the user selects a category, Flask dispatches the configured workflow, polls its run, downloads the artifact, and diagnoses the fetched log.

All three flows use the same diagnosis pipeline and display category, root cause, confidence, remediation, references, source, and generated log where applicable.

A user should be able to:

1. Start the Flask application.
2. Open the three-section web interface.
3. Analyze a manual log, generate a local log, or generate a GitHub log.
4. Receive an automated diagnosis.
5. Review the root cause and recommended fix.
6. Inspect retrieved supporting references.
7. Access previous diagnosis records through the history endpoint.

## 3. Application Startup Procedure

When the application starts through `run.py`:

1. `create_app()` creates the Flask application.
2. Configuration is loaded from `config.py` and environment variables.
3. Flask-CORS is enabled.
4. A persistent ChromaDB vector store is opened.
5. The knowledge base is seeded if it is empty.
6. The Sentence Transformer embedding model is initialized.
7. The LangGraph diagnosis agent is created.
8. The vector store and agent are attached to `app.extensions`.
9. The GitHub generation service is registered; it remains inactive until GitHub configuration is supplied.
10. The main, diagnosis, and generation blueprints are registered.
11. The server listens on `http://localhost:5000`.

## 4. Diagnosis Procedure

### Step 1: Submit a log

The client sends a request to:

```http
POST /api/diagnose
Content-Type: application/json
```

Example request:

```json
{
  "log": "ModuleNotFoundError: No module named requests"
}
```

### Step 2: Validate the request

The API checks that:

- The request contains JSON.
- The `log` field exists.
- The log is not empty.

Invalid requests return HTTP 400 with an error message.

### Step 3: Parse the log

`app/utils/log_parser.py`:

1. Splits the raw log into lines.
2. Extracts lines containing error-related terms.
3. Detects a preliminary failure category using regex patterns.
4. Builds a short summary for the agent and retrieval system.

The parser provides a category hint, but it does not replace the final LLM diagnosis.

### Step 4: Retrieve supporting context

For RAG modes, the agent sends a query to ChromaDB using the parsed log information.

The vector store:

1. Embeds the query with `all-MiniLM-L6-v2`.
2. Performs cosine-similarity search.
3. Retrieves the configured number of nearest chunks.
4. Returns text, metadata, and similarity distance.

For the documentation-only condition, historical `feedback_loop` records are excluded. For the full RAG condition, documentation and historical failure-fix pairs are available.

### Step 5: Reason with the agent

The LangGraph ReAct agent:

1. Receives the parsed log and category hint.
2. Uses the retrieval tool when operating in a RAG mode.
3. Reasons about the likely failure category.
4. Determines the probable root cause.
5. Estimates confidence.
6. Produces remediation instructions.
7. Returns a JSON diagnosis.

Expected response structure:

```json
{
  "failure_category": "dependency_error",
  "root_cause": "The requests dependency is not installed in the CI environment.",
  "confidence_score": 0.87,
  "recommended_fix": "Add requests to requirements.txt and install dependencies before running tests.",
  "retrieved_references": ["dependency_docs"]
}
```

### Step 6: Validate and return the diagnosis

The agent output is parsed from JSON. If the model returns malformed output, the application uses a fallback diagnosis rather than crashing.

The diagnosis is returned as the HTTP response from `/api/diagnose`.

### Step 7: Store feedback and history

After a diagnosis:

1. The failure summary and diagnosis are added to ChromaDB as a historical failure-fix pair.
2. A history entry is appended to the configured history file.
3. The next full-RAG request may retrieve this historical information.

## 5. Available API Endpoints

### Health check

```http
GET /health
```

Confirms that the service is running.

### Diagnose a failure

```http
POST /api/diagnose
```

Accepts a raw CI/CD failure log and returns a structured diagnosis.

### Generate a local log

```http
POST /api/generate/local
```

Creates a safe Python-generated log and returns its diagnosis and separate ground-truth metadata. Use `category: random` for random category selection, an integer `seed` for reproducibility, and `use_ai_variation` to request optional AI wording variation.

### Generate a GitHub Actions log

```http
POST /api/github/generate
GET /api/github/generate/<job_id>
```

The first endpoint dispatches the configured workflow and returns a job ID. The second endpoint reports progress and eventually returns the downloaded log and diagnosis. The token remains server-side.

### Diagnosis history

```http
GET /api/history
```

Returns previously stored diagnosis records, including the source (`manual_upload`, `local_generated`, or `github_actions`).

### Knowledge-base statistics

```http
GET /api/knowledge-base/stats
```

Returns the current number of ChromaDB chunks.

## 6. Knowledge-Base Procedure

The knowledge base is initially populated from curated documents in `app/rag/seeder.py`.

The seeded information covers:

- GitHub Actions workflow troubleshooting.
- Python and Node.js dependency failures.
- Docker image, Dockerfile, and cache failures.
- Flaky tests and assertion regressions.
- Terraform and Kubernetes configuration errors.

Documents are split into overlapping chunks, embedded, and stored persistently in ChromaDB.

The feedback loop adds new failure-fix pairs after diagnosis so the historical knowledge base can grow over time.

## 7. Evaluation Procedure

The project evaluates whether RAG improves failure diagnosis compared with an LLM-only baseline.

### Evaluation scenarios

The evaluation corpus contains 18 synthetic GitHub Actions failure scenarios:

- 5 dependency scenarios.
- 5 Docker scenarios.
- 4 test scenarios.
- 4 IaC scenarios.

Each scenario contains:

- Unique scenario ID.
- Failure category.
- Synthetic raw log.
- Expected root cause.
- Remediation keywords.
- Synthetic-data source marker.

### Evaluation conditions

Each scenario is evaluated under three conditions:

1. `llm_only` — the model receives no retrieval tool.
2. `rag_docs` — the model retrieves curated documentation only.
3. `rag_full` — the model retrieves documentation and historical failure-fix pairs.

This produces up to 54 diagnosis records: 18 scenarios multiplied by 3 conditions.

### Running the evaluation

The evaluation runner:

1. Loads the selected scenarios.
2. Builds the appropriate agent for each condition.
3. Parses every scenario log.
4. Runs the diagnosis.
5. Measures elapsed diagnosis time.
6. Stores the ground truth and diagnosis together.
7. Writes `raw_results.json` incrementally.

### Calculated metrics

The metrics module calculates:

- Diagnostic category accuracy.
- Remediation actionability using keyword coverage.
- Mean and standard deviation of diagnosis time.
- Mean confidence score.
- Per-category performance.
- NDCG retrieval scores.
- Confusion matrices.

The final report is serialized to JSON and can also be printed as a summary.

## 8. Testing Procedure

The test suite should be run with:

```bash
pytest -q
```

Tests cover:

- Health endpoint behavior.
- Diagnosis request validation.
- Diagnosis response structure.
- History persistence.
- Knowledge-base statistics.
- Log parser classification.
- Scenario corpus integrity.
- Evaluation conditions.
- Evaluation runner output.
- Metric calculations and serialization.

The current suite also covers local generation, source tracking, GitHub route validation, and artifact extraction. The GitHub integration requires valid test-repository credentials only when running the live third section; unit tests do not call GitHub.

## 9. Definition of Completion

The task is complete when:

- The application starts without unexpected dependency or initialization errors.
- The web UI and API accept manually uploaded CI/CD logs.
- Python can generate and diagnose local synthetic logs.
- GitHub Actions can be dispatched and its artifacts can be diagnosed when configured.
- The API returns validated structured diagnoses.
- RAG retrieves relevant supporting evidence.
- Diagnosis history is persisted reliably.
- Historical failure-fix pairs are reused in full-RAG mode.
- All three evaluation conditions run successfully.
- The complete scenario corpus produces raw results and metrics.
- The test suite passes.
- The implementation matches the proposal and implementation guide.
