# GitHub Actions Setup Guide

This guide explains how to connect the Flask application to GitHub Actions so that the **Generate with GitHub Actions** section can:

1. Start the failure-generation workflow.
2. Wait for GitHub Actions to finish.
3. Download the generated failure-log artifact.
4. Send the log to the diagnosis system.
5. Display the diagnosis in the frontend.

You need these three environment variables:

```env
GITHUB_TOKEN
GITHUB_REPOSITORY
GITHUB_WORKFLOW_ID
```

---

## 1. Create or choose a test repository

Use a separate GitHub repository for this college project. Do not use a production repository.

The repository should contain this project and this workflow file:

```text
.github/workflows/generate-failure-logs.yml
```

The repository must be pushed to GitHub before the Flask application can start a workflow.

Example repository address:

```text
https://github.com/alex/example-cicd-diagnosis
```

For the configuration value, use only:

```text
alex/example-cicd-diagnosis
```

Do not include:

- `https://github.com/`
- `.git`
- Extra spaces

---

## 2. Push the project to GitHub

From the project folder, make sure the workflow file is included in the Git repository.

You can check the file location manually:

```text
.github/
└── workflows/
    ├── ci.yml
    └── generate-failure-logs.yml
```

After pushing, open the repository on GitHub and select the **Actions** tab.

You should see a workflow named:

```text
Generate Realistic CI/CD Failure Logs
```

If GitHub shows a message asking you to enable Actions, enable Actions for this test repository.

---

## 3. Create a GitHub access token

The Flask backend needs permission to start workflows and download artifacts.

### Open the token page

1. Sign in to GitHub.
2. Click your profile picture.
3. Select **Settings**.
4. Select **Developer settings** at the bottom of the left menu.
5. Select **Personal access tokens**.
6. Select **Fine-grained tokens**.
7. Select **Generate new token**.

### Configure the token

Use settings similar to these:

| Setting | Value |
| --- | --- |
| Token name | `cicd-diagnosis-test` |
| Expiration | A short period suitable for the assignment |
| Resource owner | Your GitHub account or organization |
| Repository access | Only selected repositories |
| Selected repository | Your test repository |

Under **Repository permissions**, select:

| Permission | Access |
| --- | --- |
| Actions | Read and write |
| Contents | Read-only |

`Actions: Read and write` is needed because the Flask application dispatches a workflow. `Contents: Read-only` allows GitHub workflow and repository information to be read.

The exact permission screen can change slightly on GitHub. The important requirement is that the token can:

- Create a workflow dispatch event.
- Read workflow-run status.
- Read workflow artifacts.

### Copy the token

After selecting **Generate token**, GitHub displays the token once.

Copy it immediately and store it safely.

A token usually looks similar to:

```text
github_pat_********************************
```

Never paste the token into:

- The frontend JavaScript.
- `index.html`.
- A GitHub issue.
- A screenshot.
- A public document.
- A Git commit.

If the token is exposed, revoke it and create a new one.

---

## 4. Configure the project `.env` file

Open the `.env` file in the project root and add these values:

```env
GITHUB_TOKEN=github_pat_your_token_here
GITHUB_REPOSITORY=alex/example-cicd-diagnosis
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
GITHUB_DEFAULT_REF=main
```

Use your own values:

### `GITHUB_TOKEN`

This is the fine-grained token created in the previous step.

Example:

```env
GITHUB_TOKEN=github_pat_1234567890abcdefghijklmnop
```

### `GITHUB_REPOSITORY`

Use the GitHub owner and repository name:

```env
GITHUB_REPOSITORY=owner/repository-name
```

Example:

```env
GITHUB_REPOSITORY=alex/example-cicd-diagnosis
```

### `GITHUB_WORKFLOW_ID`

Use the workflow filename:

```env
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
```

Do not use the display name. Do not add `.github/workflows/`.

The GitHub API accepts either a workflow filename or numeric workflow ID. The filename is easier for this project.

### `GITHUB_DEFAULT_REF`

Use the branch containing the workflow:

```env
GITHUB_DEFAULT_REF=main
```

If your default branch is called `master`, use:

```env
GITHUB_DEFAULT_REF=master
```

The branch must exist on GitHub.

---

## 5. Keep the token private

The project `.gitignore` excludes `.env`, but check before committing:

```text
.env
```

must remain ignored.

You can safely share this file with classmates:

```text
.env.example
```

Do not place your real token in `.env.example`.

A safe `.env.example` contains placeholders:

```env
GITHUB_TOKEN=github-token-for-test-repository
GITHUB_REPOSITORY=owner/repository
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
GITHUB_DEFAULT_REF=main
```

---

## 6. Restart the Flask application

Environment variables are loaded when Flask starts. Stop the running server and start it again:

```bash
python run.py
```

Open the application:

```text
http://localhost:5000
```

The **Generate with GitHub Actions** section should now be available.

---

## 7. Test the integration from the frontend

1. Open the frontend.
2. Go to **Section 3 — Generate with GitHub Actions**.
3. Select a category, such as **Dependency**.
4. Confirm the ref is correct, usually `main`.
5. Click **Generate GitHub log**.
6. Watch the status messages:

```text
Dispatching workflow...
Status: dispatched
Status: running
Status: artifact_downloading
Status: diagnosing
Status: completed
```

When the workflow completes, the frontend displays:

- The GitHub-generated failure log.
- The detected category.
- The root cause.
- Confidence.
- Recommended fix.
- A **Verify with AI** button.

The GitHub Actions run can also be viewed in the repository's **Actions** tab.

---

## 8. Test the workflow manually first

Before testing from Flask, run the workflow manually in GitHub:

1. Open the repository's **Actions** tab.
2. Select **Generate Realistic CI/CD Failure Logs**.
3. Click **Run workflow**.
4. Select a category or `all`.
5. Click **Run workflow**.
6. Wait for the workflow to finish.
7. Open the completed run.
8. Confirm that an artifact named similar to this exists:

```text
failure-log-DEP-GHA-001
```

The artifact should contain:

```text
failure-DEP-GHA-001.log
ground_truth.json
```

If the manual run works, the Flask integration should be able to use the same workflow.

---

## 9. Optional API test

With Flask running, you can start a GitHub generation job through the API:

```bash
curl -X POST http://localhost:5000/api/github/generate \
  -H "Content-Type: application/json" \
  -d '{"category":"dependency_error","ref":"main"}'
```

The response should contain a job ID:

```json
{
  "job_id": "gha-abc123456789",
  "category": "dependency_error",
  "ref": "main",
  "status": "dispatched"
}
```

Check the job status by replacing the job ID:

```bash
curl http://localhost:5000/api/github/generate/gha-abc123456789
```

Repeat the status request until the status becomes:

```text
completed
```

---

## 10. Common errors and solutions

### Error: GitHub integration requires variables

```text
GitHub integration requires GITHUB_TOKEN, GITHUB_REPOSITORY, and GITHUB_WORKFLOW_ID
```

Check that all three values exist in `.env` and restart Flask.

Example:

```env
GITHUB_TOKEN=github_pat_your_token_here
GITHUB_REPOSITORY=owner/repository
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
```

### Error: 401 Bad credentials

Possible causes:

- The token was copied incorrectly.
- The token was revoked.
- The token expired.
- There is an extra space in the `.env` value.

Create a new token if necessary and restart Flask.

### Error: 403 Resource not accessible by personal access token

Check the token settings:

- The correct test repository is selected.
- **Actions** permission is set to **Read and write**.
- The token belongs to an account that can access the repository.
- Organization policies are not blocking fine-grained tokens.

### Error: 404 Not Found

Check:

```env
GITHUB_REPOSITORY=owner/repository
```

The owner and repository name must be correct. Do not use the full browser URL.

### Error: Workflow not found

Check:

```env
GITHUB_WORKFLOW_ID=generate-failure-logs.yml
```

Also confirm that the file exists on the selected branch:

```text
.github/workflows/generate-failure-logs.yml
```

### Error: Workflow dispatch failed with 422

Possible causes:

- The `main` branch does not exist.
- The workflow is not present on the default branch.
- The workflow does not contain `workflow_dispatch`.
- The category input does not match one of the available options.

Confirm that the workflow contains:

```yaml
on:
  workflow_dispatch:
```

### Error: No failure-log artifact found

Open the GitHub Actions run and check whether the artifact was uploaded.

The workflow must reach the artifact-upload step. The upload step uses:

```yaml
if: always()
```

Also check that the artifact has a name such as:

```text
failure-log-DEP-GHA-001
failure-log-DOC-GHA-001
failure-log-TST-GHA-001
failure-log-IAC-GHA-001
```

### GitHub log appears but diagnosis fails

GitHub retrieval and AI diagnosis are separate steps. Confirm that the main AI configuration is also set:

```env
OPENAI_API_KEY=your-openai-api-key
```

The GitHub token only starts workflows and downloads artifacts. It is not used for diagnosis.

---

## 11. Important security rules

For this college project:

- Use a separate test repository.
- Use synthetic failure logs only.
- Use a short token expiration period.
- Give the token access only to the test repository.
- Never place the token in frontend code.
- Never print the token in a log.
- Never commit `.env`.
- Do not use production credentials.
- Do not add deployment or infrastructure-apply commands to the failure workflow.

The GitHub workflow in this project creates controlled failures only. It does not deploy applications or change production infrastructure.

---

## 12. Official GitHub documentation

- [Create a workflow dispatch event](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [GitHub Actions workflow permissions](https://docs.github.com/en/actions/using-workflows/workflow-syntax-for-github-actions#permissions)
- [Download workflow artifacts](https://docs.github.com/en/rest/actions/artifacts#download-an-artifact)
- [Fine-grained personal access tokens](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/creating-a-fine-grained-personal-access-token)
