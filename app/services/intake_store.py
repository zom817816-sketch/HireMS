"""Small local audit store for email ingestion and Bitable exports.

It deliberately stores only message/attachment fingerprints and operation logs,
never resume contents or email passwords. Resume data continues through the
existing screening pipeline.
"""
from __future__ import annotations

import sqlite3
import json
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
                """CREATE TABLE IF NOT EXISTS candidate_workflow (
                candidate_id TEXT PRIMARY KEY, job_name TEXT NOT NULL, status TEXT NOT NULL,
                payload TEXT NOT NULL, owner_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                last_notified_at TEXT)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS interview (
                interview_id TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, round_name TEXT NOT NULL,
                interviewer_ids TEXT NOT NULL, start_at TEXT NOT NULL, end_at TEXT NOT NULL,
                location TEXT, note TEXT, calendar_event_id TEXT, status TEXT NOT NULL,
                feedback TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"""
            )
            conn.execute(
                """CREATE TABLE IF NOT EXISTS notification_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT, candidate_id TEXT, kind TEXT NOT NULL,
                channel TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL, detail TEXT NOT NULL)"""
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

    def upsert_candidate(self, candidate: dict, job_name: str) -> dict:
        """Create a queue item once; a newer screen refreshes its scoring payload."""
        now = datetime.now().isoformat(timespec="seconds")
        candidate_id = candidate["id"]
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT status, owner_id, created_at FROM candidate_workflow WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            is_new = not bool(existing)
            if existing:
                status, owner_id, created_at = existing
                conn.execute(
                    "UPDATE candidate_workflow SET job_name=?, payload=?, updated_at=? WHERE candidate_id=?",
                    (job_name, json.dumps(candidate, ensure_ascii=False), now, candidate_id),
                )
            else:
                status, owner_id, created_at = "待复核", None, now
                conn.execute(
                    "INSERT INTO candidate_workflow VALUES (?, ?, ?, ?, ?, ?, ?, NULL)",
                    (candidate_id, job_name, status, json.dumps(candidate, ensure_ascii=False), owner_id, now, now),
                )
        return {**candidate, "job_name": job_name, "status": status, "owner_id": owner_id, "created_at": created_at, "updated_at": now, "_created": is_new}

    def list_candidates(self, status: str | None = None) -> list[dict]:
        query = "SELECT candidate_id, job_name, status, payload, owner_id, created_at, updated_at, last_notified_at FROM candidate_workflow"
        params: tuple = ()
        if status:
            query += " WHERE status = ?"
            params = (status,)
        query += " ORDER BY updated_at DESC"
        with self._connect() as conn:
            rows = conn.execute(query, params).fetchall()
        return [
            {**json.loads(row[3]), "id": row[0], "job_name": row[1], "status": row[2], "owner_id": row[4],
             "created_at": row[5], "updated_at": row[6], "last_notified_at": row[7]}
            for row in rows
        ]

    def get_candidate(self, candidate_id: str) -> dict | None:
        values = [item for item in self.list_candidates() if item["id"] == candidate_id]
        return values[0] if values else None

    def update_candidate(self, candidate_id: str, status: str, owner_id: str | None = None) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            if owner_id is None:
                cursor = conn.execute("UPDATE candidate_workflow SET status=?, updated_at=? WHERE candidate_id=?", (status, now, candidate_id))
            else:
                cursor = conn.execute("UPDATE candidate_workflow SET status=?, owner_id=?, updated_at=? WHERE candidate_id=?", (status, owner_id, now, candidate_id))
            if cursor.rowcount == 0:
                raise KeyError(candidate_id)
        return self.get_candidate(candidate_id)  # type: ignore[return-value]

    def create_interview(self, interview: dict) -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO interview VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (interview["interview_id"], interview["candidate_id"], interview["round_name"], json.dumps(interview["interviewer_ids"]),
                 interview["start_at"], interview["end_at"], interview.get("location", ""), interview.get("note", ""),
                 interview.get("calendar_event_id"), interview.get("status", "已安排"), "", now, now),
            )
        return self.get_interview(interview["interview_id"])  # type: ignore[return-value]

    def get_interview(self, interview_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM interview WHERE interview_id=?", (interview_id,)).fetchone()
        if not row:
            return None
        return {"interview_id": row[0], "candidate_id": row[1], "round_name": row[2], "interviewer_ids": json.loads(row[3]),
                "start_at": row[4], "end_at": row[5], "location": row[6], "note": row[7], "calendar_event_id": row[8],
                "status": row[9], "feedback": row[10], "created_at": row[11], "updated_at": row[12]}

    def list_interviews(self, candidate_id: str | None = None) -> list[dict]:
        query, params = "SELECT interview_id FROM interview", ()
        if candidate_id:
            query += " WHERE candidate_id=?"
            params = (candidate_id,)
        query += " ORDER BY start_at"
        with self._connect() as conn:
            ids = [row[0] for row in conn.execute(query, params).fetchall()]
        return [self.get_interview(interview_id) for interview_id in ids if self.get_interview(interview_id)]

    def update_interview(self, interview_id: str, status: str, feedback: str = "") -> dict:
        now = datetime.now().isoformat(timespec="seconds")
        with self._connect() as conn:
            cursor = conn.execute("UPDATE interview SET status=?, feedback=?, updated_at=? WHERE interview_id=?", (status, feedback, now, interview_id))
            if cursor.rowcount == 0:
                raise KeyError(interview_id)
        return self.get_interview(interview_id)  # type: ignore[return-value]

    def notification(self, candidate_id: str | None, kind: str, channel: str, status: str, detail: str) -> None:
        with self._connect() as conn:
            conn.execute("INSERT INTO notification_log(candidate_id, kind, channel, status, created_at, detail) VALUES (?, ?, ?, ?, ?, ?)",
                         (candidate_id, kind, channel, status, datetime.now().isoformat(timespec="seconds"), detail[:1000]))

    def stale_candidates(self, hours: int) -> list[dict]:
        cutoff = datetime.now().timestamp() - hours * 3600
        return [candidate for candidate in self.list_candidates("待复核") if datetime.fromisoformat(candidate["created_at"]).timestamp() <= cutoff]
