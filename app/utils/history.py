"""Reliable SQLite-backed storage for diagnosis history."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class HistoryStore:
    """Persist diagnosis records and provide paginated queries."""

    def __init__(self, path: str, default_page_size: int = 20, max_page_size: int = 100):
        self.path = Path(path)
        self.default_page_size = default_page_size
        self.max_page_size = max_page_size
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(str(self.path), timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS diagnoses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    log_hash TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    log_summary TEXT NOT NULL,
                    failure_category_hint TEXT NOT NULL,
                    failure_category TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'manual_upload',
                    diagnosis_json TEXT NOT NULL
                )
                """
            )
            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(diagnoses)").fetchall()
            }
            if "source" not in columns:
                connection.execute(
                    "ALTER TABLE diagnoses ADD COLUMN source TEXT NOT NULL DEFAULT 'manual_upload'"
                )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnoses_timestamp ON diagnoses(timestamp)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_diagnoses_category ON diagnoses(failure_category)"
            )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "log_hash": row["log_hash"],
            "timestamp": row["timestamp"],
            "log_summary": row["log_summary"],
            "failure_category_hint": row["failure_category_hint"],
            "source": row["source"],
            "diagnosis": json.loads(row["diagnosis_json"]),
        }

    def add(
        self,
        raw_log: str,
        log_summary: str,
        failure_category_hint: str,
        diagnosis: dict[str, Any],
        source: str = "manual_upload",
    ) -> dict[str, Any]:
        """Store one diagnosis and return the serialized history entry."""
        timestamp = self._now()
        log_hash = hashlib.sha256(raw_log.encode("utf-8")).hexdigest()
        failure_category = str(diagnosis.get("failure_category", "unknown"))
        diagnosis_json = json.dumps(diagnosis, sort_keys=True)

        with self._connect() as connection:
            cursor = connection.execute(
                """
                INSERT INTO diagnoses (
                    log_hash, timestamp, log_summary, failure_category_hint,
                    failure_category, source, diagnosis_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_hash,
                    timestamp,
                    log_summary,
                    failure_category_hint,
                    failure_category,
                    source,
                    diagnosis_json,
                ),
            )
            row = connection.execute(
                "SELECT * FROM diagnoses WHERE id = ?", (cursor.lastrowid,)
            ).fetchone()
        return self._entry_from_row(row)

    @staticmethod
    def _date_bound(value: str | None, *, end_of_day: bool = False) -> str | None:
        if value is None or not value.strip():
            return None
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"Invalid ISO date/time: {value}") from exc
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if end_of_day and len(value.strip()) == 10:
            parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
        return parsed.astimezone(timezone.utc).isoformat()

    def list(
        self,
        *,
        page: int = 1,
        page_size: int | None = None,
        category: str | None = None,
        from_date: str | None = None,
        to_date: str | None = None,
    ) -> dict[str, Any]:
        """Return history records with optional category/date filters."""
        if page < 1:
            raise ValueError("page must be at least 1")
        page_size = self.default_page_size if page_size is None else page_size
        if page_size < 1 or page_size > self.max_page_size:
            raise ValueError(f"page_size must be between 1 and {self.max_page_size}")

        start = self._date_bound(from_date)
        end = self._date_bound(to_date, end_of_day=True)
        if start and end and start > end:
            raise ValueError("from must be earlier than or equal to to")

        conditions: list[str] = []
        parameters: list[Any] = []
        if category:
            conditions.append("failure_category = ?")
            parameters.append(category)
        if start:
            conditions.append("timestamp >= ?")
            parameters.append(start)
        if end:
            conditions.append("timestamp <= ?")
            parameters.append(end)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        offset = (page - 1) * page_size

        with self._connect() as connection:
            total = connection.execute(
                f"SELECT COUNT(*) FROM diagnoses {where}", parameters
            ).fetchone()[0]
            rows = connection.execute(
                f"""
                SELECT * FROM diagnoses
                {where}
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*parameters, page_size, offset],
            ).fetchall()

        return {
            "diagnoses": [self._entry_from_row(row) for row in rows],
            "page": page,
            "page_size": page_size,
            "total": total,
            "pages": (total + page_size - 1) // page_size if total else 0,
        }
