"""ChromaDB-backed retrieval and feedback storage."""

from __future__ import annotations

import hashlib
import json
import os
import textwrap
from typing import Any

import chromadb
import tiktoken
from chromadb.config import Settings

from config import Config


def _make_id(text: str, metadata: dict[str, Any] | None = None, chunk_index: int = 0) -> str:
    """Create a deterministic ID that distinguishes identical text from sources."""
    payload = json.dumps(
        {"text": text, "metadata": metadata or {}, "chunk_index": chunk_index},
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]


def _chunk_text(
    text: str,
    chunk_size: int,
    overlap: int,
    encoding_name: str = "cl100k_base",
) -> list[str]:
    """Split text into overlapping token-based chunks."""
    if not text.strip():
        return []
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("overlap must be non-negative and smaller than chunk_size")

    encoding = tiktoken.get_encoding(encoding_name)
    tokens = encoding.encode(text)
    step = chunk_size - overlap
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        chunk = encoding.decode(tokens[start : start + chunk_size]).strip()
        if chunk:
            chunks.append(chunk)
        if start + chunk_size >= len(tokens):
            break
    return chunks


class VectorStore:
    """Thin wrapper around a persistent ChromaDB collection."""

    COLLECTION_NAME = "cicd_knowledge"

    def __init__(
        self,
        persist_dir: str | None = None,
        *,
        embedding_model: str | None = None,
        embedding_tokenizer: str | None = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        top_k: int | None = None,
        embedder: Any | None = None,
    ):
        self.persist_dir = persist_dir or Config.CHROMA_PERSIST_DIR
        self.embedding_model = embedding_model or Config.EMBEDDING_MODEL
        self.embedding_tokenizer = embedding_tokenizer or Config.EMBEDDING_TOKENIZER
        self.chunk_size = chunk_size or Config.CHUNK_SIZE
        self.chunk_overlap = chunk_overlap if chunk_overlap is not None else Config.CHUNK_OVERLAP
        self.top_k = top_k or Config.TOP_K_RESULTS

        self._client = chromadb.PersistentClient(
            path=self.persist_dir,
            settings=Settings(anonymized_telemetry=False),
        )
        self._collection = self._client.get_or_create_collection(
            name=self.COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )
        self._embedder = embedder or self._load_embedder()

    def _load_embedder(self) -> Any:
        """Load Sentence Transformers without enabling optional TensorFlow paths."""
        # Sentence Transformers uses the PyTorch backend here. These flags keep
        # Transformers from importing an incompatible optional Keras/TensorFlow
        # integration in environments where only embeddings are required.
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(self.embedding_model)

    def _embed(self, texts: list[str]) -> list[list[float]]:
        return self._embedder.encode(texts, show_progress_bar=False).tolist()

    def add_documents(
        self,
        texts: list[str],
        metadatas: list[dict[str, Any]] | None = None,
        chunk: bool = True,
    ) -> int:
        """Ingest documents and return the number of chunks upserted."""
        all_chunks: list[str] = []
        all_metas: list[dict[str, Any]] = []

        for source_index, text in enumerate(texts):
            if not isinstance(text, str) or not text.strip():
                continue
            meta = (metadatas[source_index] if metadatas else {}) or {}
            chunks = (
                _chunk_text(
                    text,
                    self.chunk_size,
                    self.chunk_overlap,
                    self.embedding_tokenizer,
                )
                if chunk
                else [text.strip()]
            )
            for chunk_index, chunk_text in enumerate(chunks):
                all_chunks.append(chunk_text)
                all_metas.append({**meta, "chunk_index": chunk_index})

        if not all_chunks:
            return 0

        embeddings = self._embed(all_chunks)
        ids = [
            _make_id(text, metadata, index)
            for index, (text, metadata) in enumerate(zip(all_chunks, all_metas))
        ]
        self._collection.upsert(
            ids=ids,
            documents=all_chunks,
            embeddings=embeddings,
            metadatas=all_metas,
        )
        return len(all_chunks)

    def add_failure_fix_pair(self, log_summary: str, diagnosis: dict[str, Any]) -> None:
        """Persist a diagnosis as a historical failure-fix pair."""
        text = textwrap.dedent(
            f"""
            FAILURE CATEGORY: {diagnosis.get('failure_category', 'unknown')}
            ROOT CAUSE: {diagnosis.get('root_cause', '')}
            RECOMMENDED FIX: {diagnosis.get('recommended_fix', '')}
            LOG EXCERPT: {log_summary[:400]}
            """
        ).strip()
        self.add_documents(
            [text],
            [{
                "source": "feedback_loop",
                "category": diagnosis.get("failure_category", "unknown"),
            }],
            chunk=False,
        )

    def retrieve(
        self,
        query: str,
        k: int | None = None,
        exclude_categories: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return ranked chunks with source metadata and cosine distances."""
        k = self.top_k if k is None else k
        if k <= 0 or not query.strip():
            return []

        count = self._collection.count()
        if count == 0:
            return []

        fetch_k = min(count, k * 3) if exclude_categories else min(count, k)
        query_embedding = self._embed([query])
        results = self._collection.query(
            query_embeddings=query_embedding,
            n_results=fetch_k,
            include=["documents", "metadatas", "distances"],
        )

        output: list[dict[str, Any]] = []
        for rank, (doc, meta, distance) in enumerate(
            zip(
                results["documents"][0],
                results["metadatas"][0],
                results["distances"][0],
            ),
            start=1,
        ):
            metadata = meta or {}
            if exclude_categories and metadata.get("category") in exclude_categories:
                continue
            output.append({
                "rank": rank,
                "text": doc,
                "metadata": metadata,
                "distance": round(float(distance), 4),
            })
            if len(output) >= k:
                break
        return output

    def count(self) -> int:
        return self._collection.count()
