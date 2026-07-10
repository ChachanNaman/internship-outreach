#!/usr/bin/env python3
"""Cold-email internship outreach pipeline.

Usage:
    python outreach.py --tier 1 --dry-run     # Pune only, write drafts, no sending
    python outreach.py --tier 1 --live        # Pune only, actually send (respects --cap)
    python outreach.py --status               # print a summary of the pipeline state
    python outreach.py --skip-agencies         # exclude Staffing & Recruiting rows

Every run (aside from --status) executes the full pipeline for the selected
slice of contacts: load -> prioritize -> guess emails -> verify -> personalize
-> draft or send. Re-running is always safe: contacts that have already been
touched are never reprocessed or re-sent to.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

import emailgen
import loader
import sender
import templates
import verify
from db import Contact, Database

DEFAULT_XLSX = "Excell data.xlsx"
DEFAULT_DOCX = "946911983-Companywise-HR-Email-IDs.docx"
DEFAULT_DB = "contacts.db"
DEFAULT_CUSTOM_NOTES = "custom_note.csv"
DEFAULT_DRAFTS_DIR = "drafts"
DEFAULT_CAP = 35

TIER_LABELS = {1: "Pune", 2: "Bengaluru/Bangalore", 3: "Other major India hubs", 4: "Everything else"}


def setup_logging(log_path: str) -> logging.Logger:
    logger = logging.getLogger("outreach")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(logging.Formatter("%(message)s"))
    console_handler.setLevel(logging.WARNING)
    logger.addHandler(console_handler)

    return logger


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Internship cold-email outreach pipeline.")
    p.add_argument("--tier", type=int, choices=[1, 2, 3, 4], default=None,
                    help="Process only this tier (1=Pune, 2=Bengaluru/Bangalore, "
                         "3=other major India hubs, 4=everything else). Default: all tiers.")
    p.add_argument("--dry-run", action="store_true", default=False,
                    help="Write drafts to ./drafts instead of sending (this is the default mode).")
    p.add_argument("--live", action="store_true", default=False,
                    help="Actually send emails via Gmail SMTP. Overrides --dry-run.")
    p.add_argument("--cap", type=int, default=DEFAULT_CAP,
                    help=f"Max emails to draft/send in this run (default {DEFAULT_CAP}).")
    p.add_argument("--skip-agencies", action="store_true", default=False,
                    help="Exclude Staffing & Recruiting rows.")
    p.add_argument("--status", action="store_true", default=False,
                    help="Print a summary of the pipeline state and exit.")
    p.add_argument("--xlsx", default=DEFAULT_XLSX, help="Path to the source .xlsx file.")
    p.add_argument("--docx", default=DEFAULT_DOCX,
                    help="Path to an optional supplementary .docx contact list "
                         "(Name/Email/Title/Company table). Pass '' to skip.")
    p.add_argument("--db", default=DEFAULT_DB, help="Path to the SQLite database file.")
    p.add_argument("--custom-notes", default=DEFAULT_CUSTOM_NOTES,
                    help="Path to custom_note.csv (contact_id,note) overrides.")
    p.add_argument("--drafts-dir", default=DEFAULT_DRAFTS_DIR, help="Directory to write drafts into.")
    p.add_argument("--log-file", default="outreach.log", help="Path to the log file.")
    p.add_argument("--reference-csv", default="contacts_reference.csv",
                    help="Path to write the contact_id lookup CSV (used for custom_note.csv).")
    return p.parse_args()


def run_guess_and_verify(db: Database, logger: logging.Logger, tier, skip_agencies: bool, abstract_key: str | None) -> None:
    pending = db.fetch_new_for_processing(tier=tier, skip_agencies=skip_agencies)
    if not pending:
        return

    needs_key = any(c.company_domain for c in pending)
    if needs_key and not abstract_key:
        raise SystemExit(
            "ABSTRACT_API_KEY is not set in .env. Set it before running the pipeline "
            "(see README) -- unverified sends are not allowed."
        )

    quota_exhausted = False
    for contact in pending:
        if contact.source == "docx" and contact.candidate_emails:
            # Docx list carries hand-collected, already-correct emails --
            # trust them directly, no Abstract API call needed. Processed
            # unconditionally so an exhausted quota on xlsx contacts never
            # blocks these.
            db.set_verify_result(contact.id, contact.candidate_emails[0], "source_provided", "ready")
            logger.info("contact_id=%d verify_status=source_provided verified_email=True (docx, no API call)", contact.id)
            continue

        if quota_exhausted:
            # Leave status='new' so these are retried on the next run once the
            # key/quota issue is fixed, rather than being stuck permanently.
            continue

        if contact.candidate_emails:
            # Source already supplied a known real email -- verify that
            # directly instead of pattern-guessing.
            candidates = contact.candidate_emails
        else:
            candidates = emailgen.generate_candidates(contact.name, contact.company_domain)
            db.set_candidate_emails(contact.id, candidates)

        if not candidates:
            logger.info("No domain/candidates for contact_id=%d (%s) -> needs_manual_check", contact.id, contact.company_name)
            db.set_verify_result(contact.id, None, "needs_manual_check", "needs_manual_check")
            continue

        try:
            verified_email, verify_status = verify.verify_candidates(candidates, abstract_key)
        except verify.QuotaExceededError as exc:
            logger.warning(
                "Verification quota/auth error: %s. Remaining contacts left as 'new' for retry next run.", exc
            )
            quota_exhausted = True
            continue

        status = "ready" if verified_email else "needs_manual_check"
        db.set_verify_result(contact.id, verified_email, verify_status, status)
        logger.info("contact_id=%d verify_status=%s verified_email=%s", contact.id, verify_status, bool(verified_email))


def run_personalize_and_send(
    db: Database, logger: logging.Logger, tier, skip_agencies: bool, cap: int,
    live: bool, drafts_dir: Path, custom_notes: dict[int, str],
    gmail_address: str | None, gmail_password: str | None,
) -> dict[str, int]:
    ready = db.fetch_ready_to_send(tier=tier, skip_agencies=skip_agencies, limit=cap)
    if not ready:
        return {"drafted": 0, "sent": 0, "failed": 0}

    if live and not (gmail_address and gmail_password):
        raise SystemExit(
            "GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set in .env. "
            "Set them before running with --live (see README)."
        )

    counts = {"drafted": 0, "sent": 0, "failed": 0}
    smtp_ctx: sender.GmailSender | None = None

    def _process(contact: Contact) -> None:
        note = custom_notes.get(contact.id)
        subject, body, _ = templates.render_email(
            name=contact.name,
            company_name=contact.company_name,
            company_niche=contact.company_niche,
            agency=contact.agency,
            custom_note=note,
        )
        to_email = contact.verified_email

        if not live:
            # status stays 'ready' -- a draft is a preview, not a terminal
            # state. Only an actual send should stop this contact from being
            # picked up by a later --live run.
            path = sender.write_draft(drafts_dir, contact.id, contact.tier, contact.company_name, to_email, subject, body)
            db.set_send_result(contact.id, "drafted", "ready", path, sent=False)
            counts["drafted"] += 1
            logger.info("Drafted: contact_id=%d recipient=%s subject=%r", contact.id, to_email, subject)
            return

        try:
            smtp_ctx.send(to_email, subject, body)  # type: ignore[union-attr]
            db.set_send_result(contact.id, "sent", "sent", None, sent=True)
            counts["sent"] += 1
            logger.info("Sent: contact_id=%d recipient=%s subject=%r result=ok", contact.id, to_email, subject)
        except Exception as exc:  # noqa: BLE001
            db.set_send_result(contact.id, "not_sent", "failed", None, sent=False)
            counts["failed"] += 1
            logger.error("Send failed: contact_id=%d recipient=%s error=%s", contact.id, to_email, exc)

    if live:
        with sender.GmailSender(gmail_address, gmail_password) as opened:
            smtp_ctx = opened
            for i, contact in enumerate(ready):
                _process(contact)
                if i < len(ready) - 1:
                    sender.sleep_between_sends()
    else:
        for contact in ready:
            _process(contact)

    return counts


def print_status(db: Database) -> None:
    s = db.status_summary()
    print(f"Total contacts in DB: {s['total']}")
    print()
    print("By tier:")
    for tier in (1, 2, 3, 4):
        n = s["by_tier"].get(tier, 0)
        remaining = s["remaining_by_tier"].get(tier, 0)
        print(f"  Tier {tier} ({TIER_LABELS[tier]}): {n} total, {remaining} ready-to-send remaining")
    print()
    print("By pipeline status:")
    for status, n in sorted(s["by_status"].items()):
        print(f"  {status}: {n}")
    print()
    print("By send status:")
    for send_status, n in sorted(s["by_send_status"].items()):
        print(f"  {send_status}: {n}")
    print()
    print(f"Verified (valid email found): {s['verified']}")
    print(f"Needs manual check (no valid candidate found): {s['needs_manual_check']}")
    print(f"Sent: {s['sent']}")


def main() -> None:
    load_dotenv()
    args = parse_args()
    logger = setup_logging(args.log_file)

    live = args.live
    dry_run = not live

    db = Database(args.db)
    try:
        try:
            load_summary = loader.load_and_prioritize(args.xlsx, db)
        except loader.XlsxLoadError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            logger.error("Load failed: %s", exc)
            sys.exit(1)

        docx_summary = {"inserted": 0, "already_known": 0}
        if args.docx:
            try:
                docx_summary = loader.load_docx_contacts(args.docx, db)
            except loader.XlsxLoadError as exc:
                print(f"WARNING: skipping docx source: {exc}", file=sys.stderr)
                logger.warning("Docx load skipped: %s", exc)

        db.export_reference_csv(args.reference_csv)

        if args.status:
            print(
                f"(Loaded {load_summary['inserted']} new contact(s) from xlsx this run; "
                f"{load_summary['already_known']} already known. "
                f"Docx source: {docx_summary['inserted']} new, {docx_summary['already_known']} already known.)\n"
            )
            print_status(db)
            return

        abstract_key = os.environ.get("ABSTRACT_API_KEY")
        gmail_address = os.environ.get("GMAIL_ADDRESS")
        gmail_password = os.environ.get("GMAIL_APP_PASSWORD")
        if gmail_address:
            gmail_address = gmail_address.strip()
        if gmail_password:
            # Gmail app passwords are always alphanumeric; strip whatever
            # separator characters got pasted in (regular or non-breaking
            # spaces, etc.) from Google's "xxxx xxxx xxxx xxxx" display.
            gmail_password = "".join(c for c in gmail_password if c.isalnum())

        run_guess_and_verify(db, logger, args.tier, args.skip_agencies, abstract_key)

        custom_notes = templates.load_custom_notes(args.custom_notes)

        mode = "LIVE" if live else "DRY RUN"
        print(f"Mode: {mode}  Tier filter: {args.tier or 'all'}  Cap: {args.cap}  Skip agencies: {args.skip_agencies}")

        counts = run_personalize_and_send(
            db, logger, args.tier, args.skip_agencies, args.cap, live,
            Path(args.drafts_dir), custom_notes, gmail_address, gmail_password,
        )

        print(f"Drafted: {counts['drafted']}  Sent: {counts['sent']}  Failed: {counts['failed']}")
        if dry_run and counts["drafted"]:
            print(f"Drafts written to ./{args.drafts_dir}/ -- review them, then re-run with --live to send.")
        print("\nRun 'python outreach.py --status' for the full pipeline summary.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
