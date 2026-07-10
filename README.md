# Internship Outreach

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue) ![Platform](https://img.shields.io/badge/platform-macOS%20%7C%20Linux%20%7C%20Windows-lightgrey) ![Status](https://img.shields.io/badge/status-personal%20project-brightgreen)

A cold-email pipeline that turns a spreadsheet of company contacts into
personalized, rate-limited, idempotent outreach — without you ever
double-emailing the same person or babysitting a script.

**Load → tier by location → guess or accept a real email → verify it's
deliverable → personalize a template → draft or send.** One command runs the
whole thing; re-running it is always safe.

```
xlsx / docx  ──▶  contacts.db  ──▶  guess + verify email  ──▶  personalize
 (your data)      (SQLite,          (Abstract API, or          (niche-aware
                   idempotent)       trust a known email)        template)
                                                │
                                                ▼
                                  drafts/*.txt  (dry-run, default)
                                  Gmail SMTP    (--live, capped + rate-limited)
```

> **This repo ships the engine, not the fuel.** No spreadsheet, no
> credentials, and no personal identity are included — see
> [What you need to bring](#what-you-need-to-bring) below before you run
> anything.

---

## Table of contents

- [Quickstart](#quickstart)
- [What you need to bring](#what-you-need-to-bring)
- [3 things to personalize before your first send](#3-things-to-personalize-before-your-first-send)
- [How it works](#how-it-works)
- [Setup](#setup)
- [CLI reference](#cli-reference)
- [Daily auto-draft (optional automation)](#daily-auto-draft-optional-automation)
- [custom_note.csv](#custom_notecsv)
- [Extending the niche → focus-area mapping](#extending-the-niche--focus-area-mapping)
- [Troubleshooting](#troubleshooting)
- [Using this responsibly](#using-this-responsibly)

---

## Quickstart

No heavy setup, no infra, just a venv and two files only you can provide:

```bash
git clone https://github.com/ChachanNaman/internship-outreach.git
cd internship-outreach

python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env            # then fill in your keys (below)
# drop your own "Excell data.xlsx" and/or HR-list .docx in this folder (below)

python outreach.py --tier 1 --dry-run   # writes previews to ./drafts, sends nothing
```

Open a couple of files in `./drafts`, and once they look right:

```bash
python outreach.py --tier 1 --live      # actually sends, capped + rate-limited
```

That's it — no Docker, no database server, no cloud account. Everything
runs from your own machine into a local SQLite file.

---

## What you need to bring

This repo is intentionally missing everything that's either a secret, your
personal data, or generated at runtime. Here's exactly what's excluded
(see [`.gitignore`](.gitignore)) and what to do about each:

| Path pattern | What it is | Why it's not in the repo | What you do |
|---|---|---|---|
| `.env` | Your Abstract API key + Gmail address/app-password | Credentials | Copy `.env.example` → `.env` and fill in ([Setup](#setup)) |
| `*.xlsx` | Your source contact spreadsheet | Your own scraped/exported data (often PII) | Bring your own — [required columns](#the-xlsx-source) |
| `*.docx` | Optional supplementary HR-email list | Same — real names + emails | Bring your own — [required columns](#the-docx-source), or just omit it |
| `contacts.db` | SQLite file with every contact + pipeline status | Runtime state, contains real emails, can get large | Auto-created empty on first run |
| `drafts/` | Generated `.txt` email previews | Runtime output, contains PII | Auto-created |
| `outreach.log` | Send log (subject + recipient + result) | Runtime output, contains recipient emails | Auto-created |
| `contacts_reference.csv` | Lookup table (id → company/tier/status) | Runtime output | Auto-created |
| `custom_note.csv` | Optional hand-written per-contact override lines | Your own notes | Optional — see [custom_note.csv](#custom_notecsv) |
| `venv/` | Python virtual environment | Standard, regenerable, large | `python3 -m venv venv` |
| `__pycache__/`, `*.pyc` | Python bytecode cache | Build artifact | Nothing — ignore |
| `.DS_Store` | macOS Finder metadata | OS noise | Nothing — ignore |
| `scripts/*.log` | LaunchAgent run logs | Runtime output | Auto-created if you set up [daily auto-draft](#daily-auto-draft-optional-automation) |

Nothing above requires you to change how the tool works — every one of these
is either a file you drop in with a matching name, or something the tool
creates for you on first run.

### The xlsx source

Default filename the tool looks for: **`Excell data.xlsx`** (override with
`--xlsx PATH`). Required columns (exact names, case-sensitive):

```
Name, Job Title, Linkedin URL, Company Name, Status,
Applied for Internship/Job, Company Website, Company Linkedin,
Company Social, Company Twitter, Location, Company Niche
```

- `Location` drives tiering: `1` = Pune, `2` = Bengaluru/Bangalore,
  `3` = Hyderabad/Mumbai/Delhi/Gurgaon/Noida, `4` = everything else.
- `Company Website` is parsed for a domain, which is what email guessing
  (`first.last@domain`, etc.) is based on.
- Rows already marked `Applied for Internship/Job` are skipped automatically.
- `outreach.py` fails fast with a readable error (not a traceback) if the
  file is missing or a required column is absent.

### The docx source

Default filename: **`946911983-Companywise-HR-Email-IDs.docx`** (override
with `--docx PATH`, or pass `--docx ''` to skip this source entirely). It
must contain a table with this header row (case-insensitive):

```
Name | Email | Title | Company
```

Because these rows already carry a real, hand-collected email address, the
pipeline **trusts it directly** — no pattern-guessing, and (as of this repo)
no Abstract API call either, so a docx-sourced batch never touches your
verification quota. They default to tier 4 (no location data available)
and get no niche-based focus-area sentence.

---

## 3 things to personalize before your first send

1. **`.env`** — your own Abstract API key + Gmail address/app-password. See
   [Setup](#setup).
2. **`templates.py`** — the email body and signature are hardcoded to the
   original author's identity: name, subject line, bio, resume link,
   LinkedIn URL, and phone number (`BODY_TEMPLATE` and `SUBJECT_BASE`, near
   the top of the file). **Edit these before you send anything** — otherwise
   you'll be cold-emailing companies under someone else's name and contact
   info. Also update the bio paragraph (school, current role, projects) to
   describe you, not the original author.
3. **Your `.xlsx` / `.docx` source files** — see
   [What you need to bring](#what-you-need-to-bring) above.

Everything else (tiering, dedup, rate limiting, logging) works out of the
box with no code changes.

---

## How it works

One command runs the whole pipeline for a slice of contacts:

1. **Load & prioritize** — reads the `.xlsx`, extracts each company's domain
   from `Company Website`, tiers contacts by `Location`, flags
   `Staffing & Recruiting` rows as agencies, and skips anyone already marked
   `Applied for Internship/Job`. Everything is stored in `contacts.db`
   (SQLite) keyed by a hash of (Name, Company Name, LinkedIn URL) — so
   reloading the same spreadsheet never re-processes a contact that's
   already been touched.

   Optionally also loads a supplementary `.docx` contact list via
   `--docx PATH` (default: `DEFAULT_DOCX` in `outreach.py`, if present).
   These rows already carry a real email, so step 2 is skipped for them and
   that email is trusted directly (no API call — see
   [The docx source](#the-docx-source)). Any domain that's actually a
   social-platform URL (see `NON_COMPANY_DOMAINS` in `loader.py`) is treated
   as unusable, same as the xlsx source.
2. **Guess candidate emails** (xlsx source only) — `first.last@domain`,
   `first@domain`, `f.last@domain`, `firstlast@domain`, in that order.
3. **Verify** (xlsx source only) — tries each candidate against Abstract
   API's Email Reputation service, stops at the first result where
   deliverability is `deliverable`, SMTP/MX validation passes, the address
   isn't disposable, and risk isn't flagged `high`. If none verify, the
   contact is marked `needs_manual_check` and is never sent to. Docx-sourced
   contacts skip this step entirely and go straight to `ready`.
4. **Personalize** — fills in the fixed email template, with a focus-area
   sentence derived from `Company Niche` (dropped entirely for agencies or
   niches with no mapping), optionally overridden per-contact via
   `custom_note.csv`.
5. **Send** — dry-run by default (writes `.txt` drafts to `./drafts`);
   `--live` sends for real via Gmail SMTP, capped at `--cap` (default 35)
   per run, with a random 6-12s delay between sends and a per-recipient
   subject variation.

Every action is logged to `outreach.log` (subject + recipient + result only
— never the email body).

---

## Setup

```bash
git clone https://github.com/ChachanNaman/internship-outreach.git
cd internship-outreach
python3 -m venv venv
source venv/bin/activate        # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

### 1. Get an Abstract API key (Email Reputation product)

Only needed if you're using the xlsx source's guess-and-verify flow. If
you're only using a docx-style list of already-known emails, you can skip
this entirely.

1. Go to https://app.abstractapi.com and sign up (free tier: **100
   requests/month**, no card required). The key is generated instantly on
   signup — no manual approval needed.
2. On the dashboard, find the **Email Reputation** product card and copy its
   "Primary API key". Abstract issues a separate key per product — make
   sure you copy the one from the *Email Reputation* card specifically, not
   Phone Intelligence, IP Intelligence, or any other product.
3. Put it in `.env`:
   ```
   ABSTRACT_API_KEY=your_key_here
   ```

> 100/month is a real constraint if you're processing hundreds of contacts
> from the xlsx source — budget your `--tier` batches accordingly. If you
> outgrow it, Abstract's paid tiers or a different provider (with a code
> change to `verify.py`) are the next steps. Note this quota **does not
> apply** to docx-sourced contacts — those never call the API.

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

`.env` is gitignored — never commit it, and never hardcode credentials in
the source.

### 3. First run: dry run

```bash
python outreach.py --tier 1 --dry-run
```

This loads the spreadsheet, tiers/dedupes into `contacts.db`, guesses +
verifies emails for Pune contacts (uses Abstract API credits), personalizes,
and writes one `.txt` file per contact into `./drafts` — nothing is sent.

Open a few files in `./drafts` and read them before doing anything else.

A `contacts_reference.csv` is also written on every run (id, name, company,
tier, niche, location, status) — use it to look up `contact_id` values for
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
— respect your own daily volume comfort (30-50/day recommended; the `--cap`
flag exists exactly for this).

---

## CLI reference

```
python outreach.py --tier 1 --dry-run         # Pune only, write drafts, no sending
python outreach.py --tier 1 --live            # Pune only, actually send (respects --cap)
python outreach.py --status                   # summary: verified / sent / needs-manual-check / remaining per tier
python outreach.py --skip-agencies            # exclude Staffing & Recruiting rows
python outreach.py --tier 2 --live --cap 20   # smaller batch for tier 2
python outreach.py --docx ''                  # skip the docx source entirely for this run
```

Other flags: `--xlsx PATH`, `--docx PATH`, `--db PATH`, `--custom-notes PATH`,
`--drafts-dir PATH`, `--log-file PATH`, `--reference-csv PATH` — all default
to sensible names in the project directory.

---

## Daily auto-draft (optional automation)

A scheduled job can run `outreach.py --dry-run --cap 40` once a day on its
own, across all sources and tiers by priority. It **never sends** — it only
verifies/trusts and drafts, then pops a desktop notification with the count.
You still run one manual command to actually send. This is optional and off
by default in a fresh clone — nothing here is required to use the tool.

Files involved (macOS `launchd` example):
- `scripts/daily_draft.sh` — the script the job runs. Edit `PROJECT_DIR` at
  the top to point at your own clone path, and `DAILY_CAP` to change the
  daily draft count
- `scripts/com.example.outreach-dailydraft.plist` — a **template** you copy
  to `~/Library/LaunchAgents/` and edit for your own username/path before
  loading (the real, active plist lives outside this repo and is never
  committed, since it's machine-specific)
- `scripts/daily_draft.log` — full output of every daily run (gitignored)
- `scripts/launchd.out.log` / `scripts/launchd.err.log` — launchd-level
  stdout/stderr, should normally be empty (gitignored)

To set it up for yourself:

```bash
cp scripts/com.example.outreach-dailydraft.plist \
   ~/Library/LaunchAgents/com.yourname.outreach-dailydraft.plist

# edit the copy: replace /path/to/internship-outreach with your actual
# clone path, and adjust the Label + StartCalendarInterval (hour/minute) to taste

launchctl load ~/Library/LaunchAgents/com.yourname.outreach-dailydraft.plist
launchctl list | grep outreach-dailydraft      # confirm it's loaded
```

**Note:** the project must live outside `~/Desktop`, `~/Documents`, or
`~/Downloads` for launchd to be able to read it in the background — macOS
blocks background processes from those folders unless you separately grant
Full Disk Access.

Useful commands:
```bash
launchctl unload ~/Library/LaunchAgents/com.yourname.outreach-dailydraft.plist   # pause it
launchctl load ~/Library/LaunchAgents/com.yourname.outreach-dailydraft.plist     # resume it
```

To remove it entirely: unload it (above), then delete the `.plist` file.

Since the daily job only picks up contacts with `status='ready' AND
send_status != 'sent'`, if you let unsent drafts pile up beyond the daily
cap, the job will keep re-drafting that same backlog rather than reaching
new contacts — send what's ready reasonably often to keep it moving.

Verification credits are also a real bottleneck for the xlsx source: once
the monthly Abstract API quota (100 free/month) is used up, the daily job
will draft 0 *new* xlsx-sourced contacts until it resets, no matter the cap
— docx-sourced contacts are unaffected, since they never call the API.

*(Not on macOS? The same idea works with `cron` on Linux or Task Scheduler
on Windows — just point either at `python outreach.py --dry-run --cap N`.)*

---

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

---

## Extending the niche → focus-area mapping

`templates.py` has a `NICHE_FOCUS_AREA` dict mapping `Company Niche` values
(lowercased) to a short phrase used in the email. It covers common LinkedIn
niche categories, but your sheet may contain others. Run `--status` after a
load and check `contacts_reference.csv`'s `company_niche` column for values
with no mapping — those contacts simply get the sentence dropped, which is
safe but you may want to add a mapping instead.

---

## Troubleshooting

- **"ABSTRACT_API_KEY is not set"** — add it to `.env`, or you've hit the
  free 100/month limit (the tool marks remaining xlsx-sourced contacts
  `needs_manual_check` in that case, rather than sending unverified). Docx
  contacts are unaffected either way.
- **"quota_reached" / HTTP 429 from Abstract API** — you've used your
  monthly free verifications. Remaining xlsx contacts are left as `new` and
  retried automatically once the key/quota is fixed — nothing is lost.
- **"GMAIL_ADDRESS / GMAIL_APP_PASSWORD are not set"** — required only for
  `--live`; add both to `.env`.
- **SMTP auth errors** — confirm 2-Step Verification is on and you're using
  an App Password, not your real Gmail password.
- **"table is missing expected column(s)"** on a docx load — the first
  table in the `.docx` must have a header row containing `Name`, `Email`,
  `Title`, `Company` (case-insensitive, extra columns are fine).
- Re-running any command is always safe: loading is deduped by
  (Name, Company Name, LinkedIn URL), and sent/drafted contacts are never
  reprocessed.

---

## Using this responsibly

This is a cold-outreach tool, not a spam cannon — the defaults are built
around that:

- **Rate-limited** (6-12s randomized delay between sends) and **capped**
  (`--cap`, default 35/run) so you're not blasting hundreds of emails at
  once.
- **Personalized**, not templated-and-blank — every email carries a
  niche-derived focus line or a hand-written note, not just a mail-merge
  name swap.
- **Idempotent** — nobody gets double-emailed even if you re-run the same
  command by mistake.
- **Verified before sent** (xlsx source) — undeliverable/disposable/risky
  addresses are never mailed to, they're routed to manual review instead.

Use it for genuine, targeted outreach (internships, jobs, honest cold
intros) — not for bulk unsolicited marketing. Keep your daily volume
reasonable, and respect any company's stated no-cold-email policy.
