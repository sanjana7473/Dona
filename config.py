import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key")
    OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
    FLASK_ENV = os.environ.get("FLASK_ENV", "development")
    DEBUG = os.environ.get("FLASK_DEBUG", "1") == "1"

    CHROMA_PERSIST_DIR = os.environ.get("CHROMA_PERSIST_DIR", "./chromadb_store")
    TOP_K_RESULTS = int(os.environ.get("TOP_K_RESULTS", 5))
    CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", 512))
    CHUNK_OVERLAP = int(os.environ.get("CHUNK_OVERLAP", 64))
    DIAGNOSIS_HISTORY_PATH = os.environ.get("DIAGNOSIS_HISTORY_PATH", "./diagnosis_history.db")
    MAX_LOG_CHARS = int(os.environ.get("MAX_LOG_CHARS", 100_000))
    HISTORY_PAGE_SIZE = int(os.environ.get("HISTORY_PAGE_SIZE", 20))
    MAX_HISTORY_PAGE_SIZE = int(os.environ.get("MAX_HISTORY_PAGE_SIZE", 100))

    EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "all-MiniLM-L6-v2")
    EMBEDDING_TOKENIZER = os.environ.get("EMBEDDING_TOKENIZER", "cl100k_base")
    LLM_MODEL = os.environ.get("LLM_MODEL", "gpt-5.4-mini-2026-03-17")
    LLM_TEMPERATURE = float(os.environ.get("LLM_TEMPERATURE", 0))
    RETRIEVAL_MAX_LOG_CHARS = int(os.environ.get("RETRIEVAL_MAX_LOG_CHARS", 2_000))

    # GitHub Actions integration is optional. The application still starts and
    # local generation remains available when these values are unset.
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN")
    GITHUB_REPOSITORY = os.environ.get("GITHUB_REPOSITORY")
    GITHUB_WORKFLOW_ID = os.environ.get(
        "GITHUB_WORKFLOW_ID", "generate-failure-logs.yml"
    )
    GITHUB_DEFAULT_REF = os.environ.get("GITHUB_DEFAULT_REF", "main")
    GITHUB_API_ROOT = os.environ.get("GITHUB_API_ROOT", "https://api.github.com")
    GITHUB_HTTP_TIMEOUT = float(os.environ.get("GITHUB_HTTP_TIMEOUT", 20))
    GITHUB_POLL_INTERVAL = float(os.environ.get("GITHUB_POLL_INTERVAL", 3))
    GITHUB_RUN_TIMEOUT = float(os.environ.get("GITHUB_RUN_TIMEOUT", 300))
