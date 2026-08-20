"""Safe local storage for imported resume originals."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

from config.config import settings


SUPPORTED_TYPES = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".txt": "text/plain; charset=utf-8",
    ".md": "text/markdown; charset=utf-8",
}


class ResumeFileStore:
    """Persist and resolve resume files without exposing arbitrary local paths."""

    def __init__(self, base_dir: str | Path | None = None) -> None:
        self.base_dir = Path(base_dir or settings.RESUME_FILE_DIR).resolve()

    @staticmethod
    def _safe_resume_id(resume_id: str) -> str:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", resume_id):
            raise ValueError("Invalid resume id")
        return resume_id

    @staticmethod
    def _safe_filename(filename: str) -> str:
        name = Path(filename.replace("\\", "/")).name
        name = "".join(ch for ch in name if ch >= " " and ch not in "\r\n")[:180]
        if not name:
            raise ValueError("Resume filename is empty")
        return name

    def save(self, resume_id: str, original_filename: str, content: bytes) -> dict:
        safe_id = self._safe_resume_id(resume_id)
        safe_name = self._safe_filename(original_filename)
        suffix = Path(safe_name).suffix.lower()
        if suffix not in SUPPORTED_TYPES:
            raise ValueError("Unsupported resume file type")

        candidate_dir = self.base_dir / safe_id
        candidate_dir.mkdir(parents=True, exist_ok=True)
        target = candidate_dir / f"original{suffix}"
        fd, temporary_name = tempfile.mkstemp(prefix=".upload-", dir=candidate_dir)
        try:
            with os.fdopen(fd, "wb") as temporary:
                temporary.write(content)
            Path(temporary_name).replace(target)
        except Exception:
            Path(temporary_name).unlink(missing_ok=True)
            raise

        return {
            "resume_id": safe_id,
            "original_filename": safe_name,
            "relative_path": target.relative_to(self.base_dir).as_posix(),
            "media_type": SUPPORTED_TYPES[suffix],
            "size_bytes": len(content),
        }

    def resolve(self, relative_path: str) -> Path:
        target = (self.base_dir / relative_path).resolve()
        try:
            target.relative_to(self.base_dir)
        except ValueError as exc:
            raise ValueError("Resume path escapes storage directory") from exc
        if not target.is_file():
            raise FileNotFoundError(relative_path)
        return target

    def delete(self, relative_path: str) -> bool:
        try:
            target = self.resolve(relative_path)
        except FileNotFoundError:
            return False
        target.unlink()
        try:
            target.parent.rmdir()
        except OSError:
            pass
        return True
