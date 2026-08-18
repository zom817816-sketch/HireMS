"""IMAP intake adapter for a Feishu-hosted or other enterprise mailbox."""
from __future__ import annotations

import email
import hashlib
import imaplib
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Callable

from config.config import settings
from app.services.intake_store import IntakeStore

SUPPORTED_RESUME_EXTENSIONS = {".pdf", ".docx", ".txt", ".md"}


def _decode_header(value: str | None) -> str:
    if not value:
        return ""
    chunks: list[str] = []
    for text, charset in decode_header(value):
        chunks.append(text.decode(charset or "utf-8", errors="replace") if isinstance(text, bytes) else text)
    return "".join(chunks)


class ImapResumeIntake:
    def __init__(self, store: IntakeStore) -> None:
        self.store = store

    @staticmethod
    def configured() -> bool:
        return bool(settings.MAIL_IMAP_HOST and settings.MAIL_IMAP_USER and settings.MAIL_IMAP_PASSWORD)

    def fetch(self, import_attachment: Callable[[str, bytes, dict], str]) -> dict:
        """Fetch recent matching attachments and delegate them to the shared importer.

        ``import_attachment`` returns a resume id. The fingerprint is recorded only
        after a successful import, so a transient LLM/API failure can be retried.
        """
        if not self.configured():
            raise ValueError("邮箱尚未配置。请在 .env 填写 MAIL_IMAP_HOST / USER / PASSWORD 后重启。")

        since = (datetime.now() - timedelta(days=settings.MAIL_LOOKBACK_DAYS)).strftime("%d-%b-%Y")
        keywords = [x.strip().lower() for x in settings.MAIL_SUBJECT_KEYWORDS.split(",") if x.strip()]
        result = {"scanned": 0, "imported": 0, "skipped": 0, "failed": 0, "items": []}
        mail = imaplib.IMAP4_SSL(settings.MAIL_IMAP_HOST, settings.MAIL_IMAP_PORT)
        try:
            mail.login(settings.MAIL_IMAP_USER, settings.MAIL_IMAP_PASSWORD)
            status, _ = mail.select(settings.MAIL_IMAP_FOLDER, readonly=True)
            if status != "OK":
                raise RuntimeError(f"无法打开邮箱目录 {settings.MAIL_IMAP_FOLDER}")
            status, data = mail.search(None, "SINCE", since)
            if status != "OK":
                raise RuntimeError("邮箱检索失败")
            for uid in data[0].split():
                status, message_data = mail.fetch(uid, "(RFC822)")
                if status != "OK" or not message_data:
                    continue
                raw = next((part[1] for part in message_data if isinstance(part, tuple)), None)
                if not raw:
                    continue
                message = email.message_from_bytes(raw)
                subject = _decode_header(message.get("Subject"))
                if keywords and not any(word in subject.lower() for word in keywords):
                    continue
                message_id = message.get("Message-ID", uid.decode("ascii", errors="ignore"))
                for part in message.walk():
                    filename = _decode_header(part.get_filename())
                    if not filename or not part.get_content_disposition() == "attachment":
                        continue
                    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
                    if extension not in SUPPORTED_RESUME_EXTENSIONS:
                        continue
                    result["scanned"] += 1
                    content = part.get_payload(decode=True) or b""
                    fingerprint = hashlib.sha256((message_id + filename).encode() + content).hexdigest()
                    if self.store.already_imported(fingerprint):
                        result["skipped"] += 1
                        continue
                    try:
                        resume_id = import_attachment(filename, content, {
                            "source": "feishu_mail", "message_id": message_id, "subject": subject,
                            "from": _decode_header(message.get("From")),
                        })
                        self.store.mark_imported(fingerprint, message_id, filename)
                        result["imported"] += 1
                        result["items"].append({"filename": filename, "resume_id": resume_id})
                    except Exception as exc:  # importer needs to expose all failures in the console
                        result["failed"] += 1
                        result["items"].append({"filename": filename, "error": str(exc)})
        finally:
            try:
                mail.logout()
            except Exception:
                pass
        self.store.log("mail_sync", "success" if not result["failed"] else "partial", str(result))
        return result
