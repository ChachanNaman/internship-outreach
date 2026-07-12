"""SQLite persistence layer for the outreach tool.

Every contact is keyed by a stable hash of (name, company_name, linkedin_url)
so that re-running the loader is idempotent: a contact that has already been
inserted is never re-inserted or reset, even if the source spreadsheet is
reloaded. Downstream pipeline stages (guess/verify/send) only ever move a
contact's `status` forward -- they never revisit a contact whose status shows
it has already been processed.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterator, Optional

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_key TEXT UNIQUE NOT NULL,

    name TEXT,
    job_title TEXT,
    linkedin_url TEXT,
    company_name TEXT,
    company_website TEXT,
    company_domain TEXT,
    company_linkedin TEXT,
    company_social TEXT,
    company_twitter TEXT,
    location TEXT,
    company_niche TEXT,
    source_status TEXT,
    source TEXT NOT NULL DEFAULT 'xlsx',

    tier INTEGER,
    agency INTEGER NOT NULL DEFAULT 0,
    applied_original INTEGER NOT NULL DEFAULT 0,

    candidate_emails TEXT,
    verified_email TEXT,
    verify_status TEXT NOT NULL DEFAULT 'pending',
    verify_checked_at TEXT,

    status TEXT NOT NULL DEFAULT 'new',
    send_status TEXT NOT NULL DEFAULT 'not_sent',
    sent_at TEXT,
    draft_path TEXT,
    custom_note TEXT,

    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_contacts_status ON contacts(status);
CREATE INDEX IF NOT EXISTS idx_contacts_tier ON contacts(tier);
"""


def row_key(name: str, company_name: str, linkedin_url: str) -> str:
    raw = "|".join(
        [(name or "").strip().lower(), (company_name or "").strip().lower(), (linkedin_url or "").strip().lower()]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class Contact:
    id: int
    row_key: str
    name: str
    job_title: str
    linkedin_url: str
    company_name: str
    company_website: str
    company_domain: Optional[str]
    company_linkedin: str
    company_social: str
    company_twitter: str
    location: str
    company_niche: str
    source_status: str
    source: str
    tier: int
    agency: bool
    applied_original: bool
    candidate_emails: list
    verified_email: Optional[str]
    verify_status: str
    verify_checked_at: Optional[str]
    status: str
    send_status: str
    sent_at: Optional[str]
    draft_path: Optional[str]
    custom_note: Optional[str]
    created_at: str
    updated_at: str

    @classmethod
    def from_row(cls, r: sqlite3.Row) -> "Contact":
        d = dict(r)
        d["agency"] = bool(d["agency"])
        d["applied_original"] = bool(d["applied_original"])
        d["candidate_emails"] = json.loads(d["candidate_emails"]) if d["candidate_emails"] else []
        return cls(**d)


class Database:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        try:
            self.conn.execute("ALTER TABLE contacts ADD COLUMN source TEXT NOT NULL DEFAULT 'xlsx'")
        except sqlite3.OperationalError:
            pass  # already migrated
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @contextmanager
    def cursor(self) -> Iterator[sqlite3.Cursor]:
        cur = self.conn.cursor()
        try:
            yield cur
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        finally:
            cur.close()

    def insert_contact_if_new(self, fields: dict[str, Any]) -> bool:
        """Insert a contact row if row_key doesn't already exist.

        Returns True if a new row was inserted, False if it already existed
        (in which case nothing is touched -- idempotent reload).

        `fields` may optionally include `source` (defaults to 'xlsx') and a
        pre-known `candidate_emails` list (e.g. for sources that already
        supply a real email rather than needing name+domain guessing).
        """
        ts = now_iso()
        source = fields.get("source", "xlsx")
        candidate_emails = fields.get("candidate_emails")
        candidate_emails_json = json.dumps(candidate_emails) if candidate_emails else None
        with self.cursor() as cur:
            cur.execute(
                """
                INSERT OR IGNORE INTO contacts (
                    row_key, name, job_title, linkedin_url, company_name,
                    company_website, company_domain, company_linkedin,
                    company_social, company_twitter, location, company_niche,
                    source_status, source, tier, agency, applied_original, status,
                    candidate_emails, created_at, updated_at
                ) VALUES (
                    :row_key, :name, :job_title, :linkedin_url, :company_name,
                    :company_website, :company_domain, :company_linkedin,
                    :company_social, :company_twitter, :location, :company_niche,
                    :source_status, :source, :tier, :agency, :applied_original, :status,
                    :candidate_emails, :created_at, :updated_at
                )
                """,
                {
                    **fields,
                    "source": source,
                    "candidate_emails": candidate_emails_json,
                    "created_at": ts,
                    "updated_at": ts,
                },
            )
            return cur.rowcount > 0

    def set_candidate_emails(self, contact_id: int, candidates: list[str]) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE contacts SET candidate_emails = ?, updated_at = ? WHERE id = ?",
                (json.dumps(candidates), now_iso(), contact_id),
            )

    def set_verify_result(
        self, contact_id: int, verified_email: Optional[str], verify_status: str, status: str
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE contacts
                SET verified_email = ?, verify_status = ?, verify_checked_at = ?,
                    status = ?, updated_at = ?
                WHERE id = ?
                """,
                (verified_email, verify_status, now_iso(), status, now_iso(), contact_id),
            )

    def set_send_result(
        self, contact_id: int, send_status: str, status: str, draft_path: Optional[str], sent: bool
    ) -> None:
        with self.cursor() as cur:
            cur.execute(
                """
                UPDATE contacts
                SET send_status = ?, status = ?, draft_path = ?,
                    sent_at = CASE WHEN ? THEN ? ELSE sent_at END,
                    updated_at = ?
                WHERE id = ?
                """,
                (send_status, status, draft_path, sent, now_iso(), now_iso(), contact_id),
            )

    def fetch_new_for_processing(
        self, tier: Optional[int], skip_agencies: bool
    ) -> list[Contact]:
        """Contacts that still need email guessing + verification."""
        query = "SELECT * FROM contacts WHERE status = 'new'"
        params: list[Any] = []
        if tier is not None:
            query += " AND tier = ?"
            params.append(tier)
        if skip_agencies:
            query += " AND agency = 0"
        query += " ORDER BY tier ASC, id ASC"
        with self.cursor() as cur:
            cur.execute(query, params)
            return [Contact.from_row(r) for r in cur.fetchall()]

    def fetch_ready_to_send(
        self, tier: Optional[int], skip_agencies: bool, limit: int
    ) -> list[Contact]:
        # send_status != 'sent' (not just == 'not_sent') so contacts that were
        # only drafted (dry-run) remain eligible for a later --live send.
        query = (
            "SELECT * FROM contacts WHERE status = 'ready' AND send_status != 'sent'"
        )
        params: list[Any] = []
        if tier is not None:
            query += " AND tier = ?"
            params.append(tier)
        if skip_agencies:
            query += " AND agency = 0"
        # Randomized within tier (not alphabetical/insertion order) so a
        # --cap batch isn't always the same slice of the list -- tier
        # priority is still respected, sent contacts are still never
        # re-picked (send_status != 'sent' above), so nothing repeats.
        query += " ORDER BY tier ASC, RANDOM() LIMIT ?"
        params.append(limit)
        with self.cursor() as cur:
            cur.execute(query, params)
            return [Contact.from_row(r) for r in cur.fetchall()]

    def set_custom_note(self, contact_id: int, note: str) -> None:
        with self.cursor() as cur:
            cur.execute(
                "UPDATE contacts SET custom_note = ?, updated_at = ? WHERE id = ?",
                (note, now_iso(), contact_id),
            )

    def status_summary(self) -> dict[str, Any]:
        with self.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS n FROM contacts")
            total = cur.fetchone()["n"]

            cur.execute("SELECT tier, COUNT(*) AS n FROM contacts GROUP BY tier ORDER BY tier")
            by_tier = {r["tier"]: r["n"] for r in cur.fetchall()}

            cur.execute("SELECT status, COUNT(*) AS n FROM contacts GROUP BY status")
            by_status = {r["status"]: r["n"] for r in cur.fetchall()}

            cur.execute("SELECT send_status, COUNT(*) AS n FROM contacts GROUP BY send_status")
            by_send_status = {r["send_status"]: r["n"] for r in cur.fetchall()}

            cur.execute(
                "SELECT tier, COUNT(*) AS n FROM contacts "
                "WHERE status = 'ready' AND send_status != 'sent' GROUP BY tier ORDER BY tier"
            )
            remaining_by_tier = {r["tier"]: r["n"] for r in cur.fetchall()}

            cur.execute("SELECT COUNT(*) AS n FROM contacts WHERE verify_status = 'needs_manual_check'")
            needs_manual = cur.fetchone()["n"]

            cur.execute("SELECT COUNT(*) AS n FROM contacts WHERE send_status = 'sent'")
            sent = cur.fetchone()["n"]

            cur.execute(
                "SELECT COUNT(*) AS n FROM contacts WHERE verify_status = 'valid'"
            )
            verified = cur.fetchone()["n"]

        return {
            "total": total,
            "by_tier": by_tier,
            "by_status": by_status,
            "by_send_status": by_send_status,
            "remaining_by_tier": remaining_by_tier,
            "needs_manual_check": needs_manual,
            "sent": sent,
            "verified": verified,
        }

    def export_reference_csv(self, path: str) -> None:
        import csv

        with self.cursor() as cur:
            cur.execute(
                "SELECT id, name, company_name, tier, company_niche, location, agency, status, source "
                "FROM contacts ORDER BY tier ASC, id ASC"
            )
            rows = cur.fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["contact_id", "name", "company_name", "tier", "company_niche", "location", "agency", "status", "source"])
            for r in rows:
                writer.writerow(list(r))
