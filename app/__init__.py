"""Flask application factory for the CI/CD diagnosis service."""

import logging
from collections.abc import Callable
from typing import Any

from flask import Flask
from flask_cors import CORS

from config import Config

logger = logging.getLogger(__name__)


def _configure_quiet_dependency_logging() -> None:
    """Hide known non-actionable third-party startup noise.

    ChromaDB 0.5.x can log a telemetry-client signature mismatch even though
    queries succeed. ONNX Runtime can also warn when this CPU-only environment
    has no GPU device exposed. Keep application and agent errors visible.
    """
    for logger_name in (
        "chromadb.telemetry",
        "chromadb.telemetry.product.posthog",
        "posthog",
    ):
        noisy_logger = logging.getLogger(logger_name)
        noisy_logger.disabled = True


def create_app(
    config_class: type[Config] = Config,
    *,
    vector_store_factory: Callable[..., Any] | None = None,
    seed_func: Callable[..., int] | None = None,
    agent_factory: Callable[..., Any] | None = None,
    github_service_factory: Callable[..., Any] | None = None,
    verifier_factory: Callable[..., Any] | None = None,
):
    """Create and configure the Flask application.

    Heavy AI/vector-store dependencies are imported only when their default
    factories are needed. Tests can inject lightweight fakes without loading
    embedding models or requiring API credentials.
    """
    _configure_quiet_dependency_logging()
    app = Flask(__name__)
    app.config.from_object(config_class)
    CORS(app)

    if vector_store_factory is None:
        from app.rag.store import VectorStore

        vector_store_factory = VectorStore
    if seed_func is None:
        from app.rag.seeder import seed_knowledge_base

        seed_func = seed_knowledge_base
    if agent_factory is None:
        from app.agent.react_agent import DiagnosisAgent

        agent_factory = DiagnosisAgent

    store = vector_store_factory(
        persist_dir=app.config["CHROMA_PERSIST_DIR"],
        embedding_model=app.config["EMBEDDING_MODEL"],
        embedding_tokenizer=app.config["EMBEDDING_TOKENIZER"],
        chunk_size=app.config["CHUNK_SIZE"],
        chunk_overlap=app.config["CHUNK_OVERLAP"],
        top_k=app.config["TOP_K_RESULTS"],
    )

    if store.count() == 0:
        logger.info("Knowledge base is empty - seeding now...")
        count = seed_func(store)
        logger.info("Seeded %d chunks into ChromaDB.", count)
    else:
        logger.info("ChromaDB already contains %d chunks.", store.count())

    agent = agent_factory(
        store=store,
        mode="rag_full",
        llm_model=app.config["LLM_MODEL"],
        api_key=app.config["OPENAI_API_KEY"],
        temperature=app.config["LLM_TEMPERATURE"],
        top_k=app.config["TOP_K_RESULTS"],
    )

    from app.utils.history import HistoryStore

    app.extensions["vector_store"] = store
    app.extensions["diagnosis_agent"] = agent
    app.extensions["history_store"] = HistoryStore(
        app.config["DIAGNOSIS_HISTORY_PATH"],
        default_page_size=app.config["HISTORY_PAGE_SIZE"],
        max_page_size=app.config["MAX_HISTORY_PAGE_SIZE"],
    )

    if verifier_factory is None:
        from app.agent.verifier import DiagnosisVerifier

        verifier_factory = DiagnosisVerifier
    app.extensions["diagnosis_verifier"] = verifier_factory(
        llm_model=app.config["LLM_MODEL"],
        api_key=app.config["OPENAI_API_KEY"],
        temperature=app.config["LLM_TEMPERATURE"],
    )

    if github_service_factory is None:
        from app.integrations.github import GitHubGenerationService

        github_service_factory = GitHubGenerationService
    app.extensions["github_generation"] = github_service_factory(app)

    from app.routes.main import main_bp
    from app.routes.diagnosis import diagnosis_bp
    from app.routes.generation import generation_bp

    app.register_blueprint(main_bp)
    app.register_blueprint(diagnosis_bp, url_prefix="/api")
    app.register_blueprint(generation_bp, url_prefix="/api")

    return app
