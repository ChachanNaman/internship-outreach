"""Draft writing and Gmail SMTP sending with human-like rate limiting."""

from __future__ import annotations

import logging
import random
import re
import smtplib
import time
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

logger = logging.getLogger("outreach")

MIN_DELAY_SECONDS = 6
MAX_DELAY_SECONDS = 12


def slugify(text: str) -> str:
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower())
    return text.strip("-") or "unknown"


def write_draft(drafts_dir: Path, contact_id: int, tier: int, company_name: str, to_email: str, subject: str, body: str) -> str:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    filename = f"tier{tier}_{contact_id}_{slugify(company_name)}.txt"
    path = drafts_dir / filename
    content = f"To: {to_email}\nSubject: {subject}\n\n{body}"
    path.write_text(content, encoding="utf-8")
    return str(path)


class GmailSender:
    def __init__(self, address: str, app_password: str):
        self.address = address
        self.app_password = app_password
        self._server: Optional[smtplib.SMTP_SSL] = None

    def __enter__(self) -> "GmailSender":
        self._server = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
        self._server.login(self.address, self.app_password)
        return self

    def __exit__(self, *exc) -> None:
        if self._server is not None:
            try:
                self._server.quit()
            except Exception:  # noqa: BLE001
                pass

    def send(self, to_email: str, subject: str, body: str) -> None:
        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = self.address
        msg["To"] = to_email
        assert self._server is not None
        self._server.sendmail(self.address, [to_email], msg.as_string())


def random_delay() -> float:
    return random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)


def sleep_between_sends() -> None:
    delay = random_delay()
    logger.info("Sleeping %.1fs before next send", delay)
    time.sleep(delay)
