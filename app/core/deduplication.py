"""Stable fingerprints and identity keys used to deduplicate resumes."""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any


def normalize_resume_text(text: str) -> str:
    """Normalize harmless formatting differences without changing resume content."""
    normalized = unicodedata.normalize("NFKC", text or "").lower()
    normalized = normalized.replace("\ufeff", "").replace("\u200b", "")
    return re.sub(r"\s+", "", normalized)


def resume_fingerprint(text: str) -> str:
    return hashlib.sha256(normalize_resume_text(text).encode("utf-8")).hexdigest()


def normalize_phone(value: Any) -> str:
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) == 13 and digits.startswith("86"):
        digits = digits[2:]
    return digits if len(digits) >= 7 else ""


def normalize_email(value: Any) -> str:
    email = str(value or "").strip().lower()
    return email if "@" in email and not email.startswith("@") and not email.endswith("@") else ""


def normalize_name(value: Any) -> str:
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s·•._-]+", "", value)


def resume_deduplication_keys(text: str, metadata: dict[str, Any]) -> list[str]:
    """Return strong duplicate keys only; a name by itself is deliberately excluded."""
    keys = [f"content:{resume_fingerprint(text)}"]
    phone = normalize_phone(metadata.get("phone"))
    email = normalize_email(metadata.get("email"))
    if phone:
        keys.append(f"phone:{phone}")
    if email:
        keys.append(f"email:{email}")
    return keys
