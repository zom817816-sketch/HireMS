"""Small local audit store for email ingestion and Bitable exports.

It deliberately stores only message/attachment fingerprints and operation logs,
never resume contents or email passwords. Resume data continues through the
existing screening pipeline.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator


class IntakeStore:
    def __init__(self, db_path: str = "./data/hirems_ops.sqlite3") -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialise()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        try:
            yield connection
            connection.commit()
        finally:
            connection.close()

    def _initialise(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS imported_attachment (
                fingerprint TEXT PRIMARY KEY, message_id TEXT, filename TEXT,
                imported_at TEXT NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS operation_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL,
                kind TEXT NOT NULL, status TEXT NOT NULL, detail TEXT NOT NULL)"""
            )

    def already_imported(self, fingerprint: str) -> bool:
        with self._connect() as conn:
            return conn.execute(
                "SELECT 1 FROM imported_attachment WHERE fingerprint = ?", (fingerprint,)
            ).fetchone() is not None

    def mark_imported(self, fingerprint: str, message_id: str, filename: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO imported_attachment VALUES (?, ?, ?, ?)",
                (fingerprint, message_id, filename, datetime.now().isoformat(timespec="seconds")),
            )

    def log(self, kind: str, status: str, detail: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO operation_log(created_at, kind, status, detail) VALUES (?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), kind, status, detail[:1000]),
            )

    def recent_logs(self, limit: int = 20) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT created_at, kind, status, detail FROM operation_log ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [
            {"created_at": row[0], "kind": row[1], "status": row[2], "detail": row[3]}
            for row in rows
        ]
