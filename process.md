# Three-Source CI/CD Log Processing Plan

## 1. Is this design possible?

Yes. The project can be organized into three frontend sections based on the source of the failure log:

1. **Manual upload** — the user uploads or pastes an existing CI/CD failure log.
2. **Local random generation** — Python generates a synthetic failure log automatically, with optional AI-generated variations. No GitHub Actions run is required for this section.
3. **GitHub Actions generation** — the frontend requests a workflow run, GitHub Actions creates a realistic runner log, and the backend fetches and diagnoses it automatically.

All three sections use the same diagnosis pipeline after a log is available:

```text
log source -> validation -> parser -> RAG -> diagnosis agent -> validated result -> history
```

The existing project already supports the core diagnosis operation through:

```text
POST /api/diagnose
```

The current GitHub Actions workflow generates controlled failure logs and uploads them as artifacts. The application now also provides the local generator and connects both generation flows to the frontend and shared diagnosis pipeline.

> Section 2 is local synthetic generation. It is not continuous CI monitoring. Section 3 is the separate GitHub Actions integration.

---

## 2. The three log sources

| Section | Source | Needs GitHub? | Main purpose |
| --- | --- | ---: | --- |
| 1 | User-uploaded or pasted log | No | Analyze an existing real or synthetic log |
| 2 | Python-generated random log, optionally varied by AI | No | Create repeatable local test examples quickly |
| 3 | GitHub Actions workflow artifact | Yes | Produce realistic runner output for demonstration |

The diagnosis result should use the same display component for all three sources, while showing a source label such as `manual_upload`, `local_generated`, or `github_actions`.

Every generated log must have ground-truth metadata stored separately from the log. The metadata should include the expected category, root cause, and remediation keywords. This is necessary for fair evaluation and prevents the diagnosis agent from being given the answer in its prompt.

---

## 3. Proposed overall architecture

```text
                         +----------------------+
                         |      Frontend        |
                         |  Three user sections |
                         +----------+-----------+
                                    |
                +-------------------+-------------------+
                |                   |                   |
                v                   v                   v
       Upload existing log   Generate local log   Generate GitHub log
                |                   |                   |
                v                   v                   v
       POST /api/diagnose   POST /api/generate/local  POST /api/github/generate
                |                   |                   |
                |                   |            GitHub Actions runner
                |                   |                   |
                |                   |            Log/artifact download
                |                   |                   |
                +-------------------+-------------------+
                                    |
                                    v
                         Shared diagnosis pipeline
                                    |
                    Parser -> RAG -> AI agent -> validation
                                    |
                                    v
                         History and frontend result
```

The browser should communicate with Flask. Flask should communicate with GitHub. The browser should not receive or contain a GitHub personal access token.

---

## 4. Section 1 — Upload an existing log

### User experience

The first section contains:

- A file upload control for `.log` or `.txt` files.
- An optional text area for pasted logs.
- An **Analyze Log** button.
- A progress indicator.
- A diagnosis result card.

The result card displays:

- Failure category.
- Root cause.
- Confidence score.
- Recommended fix.
- Retrieved references.
- Timestamp and source.

### Processing flow

```text
User selects a file or pastes text
        |
        v
Frontend reads the text
        |
        v
POST /api/diagnose {"log": "..."}
        |
        v
Flask validates size and content
        |
        v
Log parser identifies error lines and category hint
        |
        v
RAG retrieves relevant knowledge
        |
        v
LangGraph/OpenAI agent creates a diagnosis
        |
        v
Pydantic validates the response
        |
        +--> Result returned to the frontend
        |
        +--> Diagnosis stored in SQLite history
        |
        +--> Failure-fix pair added to the feedback collection
```

This flow is already supported by the current application. The frontend only needs a more complete upload interface if file selection is required instead of copy-and-paste.

---

## 5. Section 2 — Locally generated random logs

### Purpose

The second section creates failure logs locally without calling GitHub Actions. This gives the project a fast and inexpensive way to generate many examples for testing and demonstration. The user selects a batch size and interval, then Python generates the logs sequentially until the batch finishes or the user presses Stop.

The generator should not create completely meaningless random text. It should select a known failure category and fill a realistic template with randomized values.

Example categories:

- Dependency resolution conflict.
- Missing Python or Node package.
- Docker image or Dockerfile failure.
- Test assertion or missing environment variable.
- Terraform or Kubernetes configuration error.

### Recommended generation process

```text
User opens the local-generation section
        |
        v
User selects a category, batch size, and interval
        |
        v
Frontend requests the next local log
        |
        v
Python selects a scenario template and random parameters
        |
        v
Optional AI variation makes the wording more realistic
        |
        v
Generator saves the log and separate ground-truth metadata
        |
        v
The log is sent to the existing diagnosis pipeline
        |
        v
A result card with its own Verify with AI button is displayed
        |
        v
The frontend waits for the interval and requests the next log
        |
        v
The batch finishes automatically or stops when the user clicks Stop
```

### Python generation strategy

A safe generator should use templates such as:

```python
{
    "category": "dependency_error",
    "template": "Cannot install {package_a}=={version_a} because it requires {dependency}<3, but {dependency}=={version_b} was requested.",
    "root_cause": "Two pinned packages have incompatible dependency constraints.",
    "remediation_keywords": ["requirements.txt", "compatible", "dependency"],
}
```

Random values can include package names, versions, image tags, test values, and environment-variable names. The selected metadata must remain attached to the generated record.

### Optional AI variation

AI can vary the wording and add realistic context, but it should not be responsible for the ground truth. The safe order is:

1. Python selects the category and ground truth.
2. Python generates the core failure facts.
3. AI optionally rewrites or expands the log while preserving those facts.
4. The system validates that important facts still appear in the output.
5. The generated log is diagnosed.

For reproducible evaluation, the project should support a deterministic Python-only mode using a random seed. AI variation should be an optional demonstration feature because it requires an API key and can change between runs.

### Suggested local-generation endpoints

The local-generation endpoint is implemented as:

```http
POST /api/generate/local
Content-Type: application/json
```

Request:

```json
{
  "category": "random",
  "use_ai_variation": false,
  "seed": 42
}
```

Response:

```json
{
  "source": "local_generated",
  "log_id": "local-123",
  "log": "...generated synthetic CI/CD log...",
  "diagnosis": {
    "failure_category": "docker_failure",
    "root_cause": "...",
    "confidence_score": 0.94,
    "recommended_fix": "...",
    "retrieved_references": ["docker_docs"]
  }
}
```

The ground truth should be stored for evaluation but should not be returned to the diagnosis agent as prompt context.

### Frontend display

The second section can show:

- Category selector: Random, Dependency, Docker, Test, or IaC.
- Number-of-logs field.
- Interval field between requests.
- Optional AI variation checkbox.
- Optional starting seed field.
- **Start generating** and **Stop** buttons.
- Progress bar and completion count.
- A separate diagnosis card for every generated log.
- A separate Verify with AI button on every diagnosis card.
- Ground-truth comparison only when running an evaluation view.

---

## 6. Section 3 — Generate a log using GitHub Actions

### User experience

The third section contains:

- A scenario selector, such as Dependency, Docker, Test, or IaC.
- A **Generate Failure Log** button.
- A status indicator showing dispatch, workflow, artifact download, and diagnosis states.
- A result area that displays the generated log and diagnosis.

The user should not need to open GitHub manually.

### Recommended request flow

```text
User selects a failure scenario
        |
        v
Frontend calls Flask
POST /api/github/generate
        |
        v
Flask validates the scenario
        |
        v
Flask calls GitHub Actions workflow_dispatch API
        |
        v
GitHub creates a workflow run
        |
        v
Flask returns a local job_id immediately
        |
        v
Frontend polls Flask for job status
GET /api/github/generate/{job_id}
        |
        v
Backend checks the GitHub run status
        |
        v
When complete, backend downloads the artifact
        |
        v
Backend extracts the .log and ground_truth.json
        |
        v
Backend sends the log to the diagnosis service
        |
        v
Frontend receives the generated log and diagnosis
```

### Why the backend must call GitHub

The GitHub token must remain server-side. A frontend request should never contain a token such as:

```text
GITHUB_TOKEN=...
```

Instead, the browser calls Flask and Flask uses a server-side credential to call GitHub. This protects the repository and prevents users from reusing the token outside the application.

### Suggested API endpoints

The GitHub-generation endpoints are implemented alongside `/api/diagnose` and `/api/history`.

#### Start a generated-log job

```http
POST /api/github/generate
Content-Type: application/json
```

Request:

```json
{
  "scenario": "dependency",
  "ref": "main"
}
```

Response:

```json
{
  "job_id": "local-job-123",
  "status": "queued",
  "message": "GitHub Actions workflow dispatched"
}
```

#### Check job status

```http
GET /api/github/generate/local-job-123
```

Possible response while running:

```json
{
  "job_id": "local-job-123",
  "status": "running",
  "github_run_id": 123456789,
  "progress": "Waiting for GitHub Actions to complete"
}
```

Possible response after diagnosis:

```json
{
  "job_id": "local-job-123",
  "status": "completed",
  "source": "github_actions_synthetic",
  "log": "...captured GitHub Actions log...",
  "diagnosis": {
    "failure_category": "dependency_error",
    "root_cause": "...",
    "confidence_score": 0.96,
    "recommended_fix": "...",
    "retrieved_references": ["dependency_docs"]
  }
}
```

#### List monitored runs

```http
GET /api/github/runs?status=failed&page=1
```

This endpoint can return recent GitHub runs and the diagnosis status for each run.

### AI verification button

After any diagnosis is displayed, the frontend shows **Verify with AI** when the original log is available. The button sends the log and proposed diagnosis to:

```http
POST /api/verify
```

The verifier is a separate AI review call. It does not receive `ground_truth.json` or other hidden evaluation metadata. It returns:

- `is_correct` — whether the proposed diagnosis is technically correct.
- `confidence_score` — reviewer confidence.
- `explanation` — why the result was accepted or rejected.
- `evidence` — relevant evidence extracted from the log.
- Corrected category, root cause, and recommended fix when needed.

This allows verification for manual uploads as well as generated logs. It requires a valid `OPENAI_API_KEY` because the button performs a new AI request.

---

## 7. Job-state model

A generated-log request should have explicit states:

```text
queued
  -> dispatched
  -> running
  -> completed
  -> artifact_downloaded
  -> diagnosing
  -> analyzed
```

Failure states should be separate and visible:

```text
dispatch_failed
workflow_failed_to_start
artifact_not_found
artifact_download_failed
diagnosis_failed
```

Keeping these states separate allows the frontend to display a useful message instead of appearing frozen while GitHub is running.

For the college project, a small local job store is sufficient. SQLite can store:

- Local job ID.
- GitHub repository and workflow ID.
- GitHub run ID.
- Requested scenario.
- Current status.
- Artifact name.
- Generated log.
- Diagnosis JSON.
- Error message.
- Created and updated timestamps.

---

## 8. GitHub workflow design

The existing workflow:

```text
.github/workflows/generate-failure-logs.yml
```

already supports `workflow_dispatch` and generates eight controlled failure scenarios. To support a frontend scenario selector, the workflow can later define an input:

```yaml
on:
  workflow_dispatch:
    inputs:
      scenario:
        description: Failure scenario to generate
        required: true
        type: choice
        options:
          - dependency
          - docker
          - test
          - iac
```

The workflow can then execute only the selected scenario. Alternatively, the current matrix can remain in place and the backend can select the artifact matching the requested scenario.

For the GitHub Actions generation flow, the workflow should use `workflow_dispatch`. A failure-log artifact should be uploaded with `if: always()` so that the backend can retrieve it after the run, including when the intentional scenario fails.

The workflow must remain safe:

- Use synthetic fixtures only.
- Do not deploy infrastructure.
- Do not modify production repositories.
- Do not include real secrets in logs.
- Do not print GitHub tokens or API keys.
- Keep artifact retention short for test data.

---

## 9. GitHub authentication and configuration

The backend needs permission to dispatch workflows and download artifacts. A repository-scoped token or GitHub App is required.

For a simple college demonstration, a fine-grained personal access token limited to the test repository can be used. It should be stored only in the server environment, for example:

```env
GITHUB_TOKEN=replace-with-a-test-repository-token
GITHUB_REPOSITORY=owner/repository
GITHUB_WORKFLOW_FILE=generate-failure-logs.yml
GITHUB_DEFAULT_REF=main
```

The exact token permissions should be the minimum required by the chosen GitHub API operations. The token should not be committed to `.env`, frontend JavaScript, HTML, screenshots, or Git history.

A stronger future implementation would use a GitHub App instead of a personal token.

---

## 10. Recommended frontend layout

```text
+-------------------------------------------------------------+
|              CI/CD Failure Diagnosis                        |
+----------------------+----------------------+---------------+
| 1. Upload Log       | 2. Local Generator   | 3. GitHub     |
|                      |                      |    Generator  |
| [Choose file]        | [Category v]         | [Scenario v]  |
| [Paste log]          | [Generate]           | [Generate]    |
| [Analyze]            | Generated log        | Job status    |
|                      | Diagnosis result     | Diagnosis     |
+----------------------+----------------------+---------------+
|                    Diagnosis Result                         |
| Category | Root cause | Confidence | Recommended fix     |
+-------------------------------------------------------------+
```

The result component should be reusable by all three sections. Whether a log came from manual upload, local Python/AI generation, or GitHub Actions should be shown as a source label.

---

## 11. Implementation phases

### Phase 1 — Frontend separation

- Split the current page into the three requested sections.
- Keep the upload section connected to `/api/diagnose`.
- Add loading, error, and empty states.
- Use one reusable diagnosis-result component for all sources.

### Phase 2 — Local Python generator

- Add scenario templates for dependency, Docker, test, and IaC failures.
- Add seeded random selection for reproducible logs.
- Add optional AI wording variation.
- Store ground-truth metadata separately.
- Add `POST /api/generate/local`.
- Send generated logs through the existing diagnosis pipeline.

### Phase 3 — GitHub Actions generation

- Add a GitHub client module.
- Add configuration for repository, workflow, ref, and token.
- Add `POST /api/github/generate`.
- Add a local job-status store.
- Dispatch the workflow through the GitHub API.

### Phase 4 — Artifact retrieval and diagnosis

- Add `GET /api/github/generate/<job_id>`.
- Poll the GitHub run until it completes.
- Download and extract the matching artifact.
- Validate the log and ground-truth metadata.
- Pass the log through the existing diagnosis pipeline.
- Save the result to diagnosis history.

### Phase 5 — Evaluation and presentation

- Compare manual, locally generated, and GitHub Actions log sources.
- Keep ground truth separate from the diagnosis prompt.
- Run the three evaluation conditions: `llm_only`, `rag_docs`, and `rag_full`.
- Report category accuracy, actionability, diagnosis time, NDCG, and confusion matrices.
- Include screenshots of the three frontend sections and a GitHub Actions run in the college-project report.

---

## 12. Important design decisions

### Do not make the frontend wait for the workflow

GitHub Actions may take from several seconds to several minutes. The generate endpoint should return a `job_id` immediately. The frontend should poll the status endpoint or subscribe to updates.

### Do not call GitHub directly from browser JavaScript

Keep the GitHub token in Flask configuration or a secret manager. Only expose a local job ID to the browser.

### Do not send ground truth to the diagnosis agent

`ground_truth.json` is for evaluation after diagnosis. It must not be included in the prompt, otherwise the evaluation would be invalid.

### Avoid duplicate diagnoses

The frontend may poll the same GitHub job more than once. Store the GitHub run ID and artifact identity so the backend does not download or diagnose the same artifact repeatedly.

### Keep generated failures isolated

The workflow should run only safe synthetic commands. It should not execute arbitrary user-provided shell commands from the frontend.

---

## 13. Current status versus target status

### Already available

- Flask web application.
- Upload/paste diagnosis flow.
- `POST /api/diagnose`.
- RAG retrieval and diagnosis agent.
- SQLite diagnosis history.
- Manually triggered GitHub Actions failure generator.
- Artifact loader for downloaded GitHub Actions artifacts.
- Evaluation runner and metrics.

### Implemented for the complete three-section application

- Three frontend sections and reusable result display.
- Python template-based random log generator.
- Seeded generation for reproducible local logs.
- Optional AI variation with fact-preservation fallback.
- Ground-truth metadata for generated logs.
- Backend GitHub API client.
- Secure server-side workflow dispatch.
- Job tracking and status polling.
- Automatic artifact download, extraction, and diagnosis.
- Tests for local generation, GitHub routes, artifact parsing, and validation.

### Optional future enhancements

- Persist GitHub jobs in SQLite so active jobs survive a Flask restart.
- Use GitHub webhooks instead of polling.
- Add a GitHub App instead of a fine-grained personal access token.
- Add authentication and rate limiting for public deployment.

---

## 14. Recommended demonstration sequence

For the college presentation:

1. Start the Flask application.
2. Open the three-section frontend.
3. Upload `sample_ci_cd_failure.log` and show the diagnosis.
4. In Section 2, generate a random local dependency or Docker log and show its diagnosis.
5. Enable optional AI variation and generate another local log if an API key is available.
6. In Section 3, click **Generate Failure Log** and select `dependency` or `docker`.
7. Show the GitHub job status changing from queued to running to diagnosing.
8. Display the fetched GitHub Actions log and diagnosis.
9. Open the history section and show that all three sources can be distinguished.
10. Run the evaluation command and compare the three RAG conditions.

This gives a realistic demonstration without connecting to production systems or applying automatic fixes.
