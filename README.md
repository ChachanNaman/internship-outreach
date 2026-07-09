# Internship Outreach

End-to-end cold-email pipeline for internship outreach: load the talent
spreadsheet, prioritize by location, guess + verify emails, personalize a
template, and send (or draft) with rate limiting -- all idempotent, so
re-running the tool never double-emails anyone.

## How it works

One command runs the whole pipeline for a slice of contacts:

1. **Load & prioritize** -- reads the `.xlsx`, extracts each company's domain
   from `Company Website`, tiers contacts by `Location`
   (1 = Pune, 2 = Bengaluru/Bangalore, 3 = Hyderabad/Mumbai/Delhi/Gurgaon/Noida,
   4 = everything else), flags `Staffing & Recruiting` rows as agencies, and
   skips anyone already marked `Applied for Internship/Job`. Everything is
   stored in `contacts.db` (SQLite) keyed by a hash of
   (Name, Company Name, LinkedIn URL) -- so reloading the same spreadsheet
   never re-processes a contact that's already been touched.
2. **Guess candidate emails** -- `first.last@domain`, `first@domain`,
   `f.last@domain`, `firstlast@domain`, in that order.
3. **Verify** -- tries each candidate against Abstract API's Email
   Reputation service, stops at the first result where deliverability is
   `deliverable`, SMTP/MX validation passes, the address isn't disposable,
   and risk isn't flagged `high`. If none verify, the contact is marked
   `needs_manual_check` and is never sent to.
4. **Personalize** -- fills in the fixed email template, with a focus-area
   sentence derived from `Company Niche` (dropped entirely for agencies or
   niches with no mapping), optionally overridden per-contact via
   `custom_note.csv`.
5. **Send** -- dry-run by default (writes `.txt` drafts to `./drafts`);
   `--live` sends for real via Gmail SMTP, capped at `--cap` (default 35) per
   run, with a random 6-12s delay between sends and a per-recipient subject
   variation.

Every action is logged to `outreach.log` (subject + recipient + result only --
never the email body).

## Setup

```bash
cd Outreach
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. Get an Abstract API key (Email Reputation product)

1. Go to https://app.abstractapi.com and sign up (free tier: **100
   requests/month**, no card required). The key is generated instantly on
   signup -- no manual approval needed.
2. On the dashboard, find the **Email Reputation** product card and copy its
   "Primary API key". Abstract issues a separate key per product -- make
   sure you copy the one from the *Email Reputation* card specifically, not
   Phone Intelligence, IP Intelligence, or any other product.
3. Put it in `.env`:
   ```
   ABSTRACT_API_KEY=your_key_here
   ```

> 100/month is a real constraint if you're processing hundreds of contacts --
> budget your `--tier` batches accordingly (e.g. don't verify tier 4 "just to
> see" if you're saving credits for tier 1-2 outreach). If you outgrow it,
> Abstract's paid tiers or a different provider (with a code change to
> `verify.py`) are the next steps.

### 2. Generate a Gmail App Password

1. Enable 2-Step Verification on the Gmail account you're sending from:
   https://myaccount.google.com/security
2. Generate an App Password: https://myaccount.google.com/apppasswords
   (choose "Mail" / "Other", copy the 16-character password).
3. Put both in `.env`:
   ```
   GMAIL_ADDRESS=you@gmail.com
   GMAIL_APP_PASSWORD=xxxxxxxxxxxxxxxx
   ```

`.env` is gitignored -- never commit it, and never hardcode credentials in
the source.

### 3. First run: dry run

```bash
python outreach.py --tier 1 --dry-run
```

This loads the spreadsheet, tiers/dedupes into `contacts.db`, guesses +
verifies emails for Pune contacts (uses Abstract API credits), personalizes,
and writes one `.txt` file per contact into `./drafts` -- nothing is sent.

Open a few files in `./drafts` and read them before doing anything else.

A `contacts_reference.csv` is also written on every run (id, name, company,
tier, niche, location, status) -- use it to look up `contact_id` values for
`custom_note.csv`.

### 4. Going live

Once you're happy with the drafts:

```bash
python outreach.py --tier 1 --live
```

This sends for real via Gmail SMTP, capped at 35 emails (override with
`--cap`), with randomized delays between sends. Every send is committed to
`contacts.db` immediately, so if the process crashes or you re-run it,
already-sent contacts are never re-sent to.

Repeat with `--tier 2`, `--tier 3`, `--tier 4` (or omit `--tier` to pull from
all tiers, highest-priority first) as you work through the list across days
-- respect your own daily volume comfort (30-50/day recommended; the `--cap`
flag exists exactly for this).

## CLI reference

```
python outreach.py --tier 1 --dry-run     # Pune only, write drafts, no sending
python outreach.py --tier 1 --live        # Pune only, actually send (respects --cap)
python outreach.py --status               # summary: verified / sent / needs-manual-check / remaining per tier
python outreach.py --skip-agencies        # exclude Staffing & Recruiting rows
python outreach.py --tier 2 --live --cap 20   # smaller batch for tier 2
```

Other flags: `--xlsx PATH`, `--db PATH`, `--custom-notes PATH`,
`--drafts-dir PATH`, `--log-file PATH`, `--reference-csv PATH` -- all default
to sensible names in the project directory.

## custom_note.csv

Optional. Hand-edit this file for high-priority companies to replace the
auto-generated focus-area sentence with something specific:

```csv
contact_id,note
14,Saw your team's Series B post on LinkedIn -- the ML infra problem you described is exactly what I want to work on.
```

Look up `contact_id` in `contacts_reference.csv`. Any row here overrides the
niche-based sentence for that contact (or fills in the sentence for
agencies/unmapped niches, if you want one there specifically).

## Extending the niche -> focus-area mapping

`templates.py` has a `NICHE_FOCUS_AREA` dict mapping `Company Niche` values
(lowercased) to a short phrase used in the email. It covers common LinkedIn
niche categories, but your sheet may contain others. Run `--status` after a
load and check `contacts_reference.csv`'s `company_niche` column for values
with no mapping -- those contacts simply get the sentence dropped, which is
safe but you may want to add a mapping instead.

## Notes on the source spreadsheet

The tool expects these columns: `Name`, `Job Title`, `Linkedin URL`,
`Company Name`, `Status`, `Applied for Internship/Job`, `Company Website`,
`Company Linkedin`, `Company Social`, `Company Twitter`, `Location`,
`Company Niche`.

The default source file is `Excell data.xlsx` in this folder. Point at a
different file with `--xlsx PATH` if needed. `outreach.py` fails fast with a
clear error (rather than a traceback) if the file is missing, unreadable, or
missing expected columns.

## Troubleshooting

- **"ABSTRACT_API_KEY is not set"** -- add it to `.env`, or you've hit the
  free 100/month limit (the tool marks remaining contacts
  `needs_manual_check` in that case, rather than sending unverified).
- **"GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set"** -- required only for
  `--live`; add both to `.env`.
- **SMTP auth errors** -- confirm 2-Step Verification is on and you're using
  an App Password, not your real Gmail password.
- Re-running any command is always safe: loading is deduped by
  (Name, Company Name, LinkedIn URL), and sent/drafted contacts are never
  reprocessed.
