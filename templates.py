"""Email template rendering: niche -> focus-area phrase mapping, custom note
overrides, and the merge-field logic for the cold outreach email.
"""

from __future__ import annotations

import csv
import logging
import os
from typing import Optional

logger = logging.getLogger("outreach")

# Company Niche -> focus-area phrase used in:
#   "[Company Name]'s work in [focus area] is exactly the kind of problem
#    space I want to go deeper on."
# Keys are matched case-insensitively against the "Company Niche" column.
# Niches with no entry here (or agency=True contacts) simply drop that
# sentence rather than forcing a generic filler line.
NICHE_FOCUS_AREA = {
    "information technology & services": "enterprise software",
    "information technology and services": "enterprise software",
    "computer software": "software",
    "software development": "software",
    "financial services": "fintech",
    "banking": "fintech",
    "investment banking": "fintech",
    "investment management": "fintech",
    "e-learning": "ed-tech",
    "education management": "ed-tech",
    "higher education": "ed-tech",
    "internet": "consumer internet products",
    "marketing & advertising": "ad-tech",
    "marketing and advertising": "ad-tech",
    "human resources": "HR & workforce solutions",
    "hospital & health care": "health-tech",
    "hospital and health care": "health-tech",
    "health, wellness & fitness": "health & wellness tech",
    "health, wellness and fitness": "health & wellness tech",
    "medical devices": "health-tech",
    "retail": "retail tech",
    "e-commerce": "e-commerce",
    "logistics & supply chain": "logistics tech",
    "logistics and supply chain": "logistics tech",
    "transportation/trucking/railroad": "logistics tech",
    "telecommunications": "telecom",
    "consumer electronics": "consumer electronics",
    "automotive": "automotive tech",
    "real estate": "proptech",
    "insurance": "insurtech",
    "management consulting": "consulting",
    "computer games": "gaming",
    "media production": "media tech",
    "broadcast media": "media tech",
    "renewables & environment": "clean-tech",
    "renewables and environment": "clean-tech",
    "biotechnology": "biotech",
    "pharmaceuticals": "pharma tech",
    "legal services": "legal tech",
    "non-profit organization management": "social impact tech",
    "semiconductors": "semiconductors",
    "aviation & aerospace": "aerospace tech",
    "aviation and aerospace": "aerospace tech",
    "food & beverages": "food-tech",
    "food and beverages": "food-tech",
    "design": "product design",
    "market research": "data & analytics",
    "information services": "data services",
    "computer networking": "networking & infrastructure",
    "venture capital & private equity": "venture-backed startups",
    "venture capital and private equity": "venture-backed startups",
    "construction": "construction tech",
    "computer hardware": "hardware",
    "airlines/aviation": "aerospace tech",
    "outsourcing/offshoring": "enterprise software",
    "computer & network security": "cybersecurity",
    "computer and network security": "cybersecurity",
    "wireless": "telecom",
    "electrical/electronic manufacturing": "hardware",
    "mechanical or industrial engineering": "industrial tech",
    "oil & energy": "energy tech",
    "oil and energy": "energy tech",
    "utilities": "energy tech",
    "government administration": "public sector technology",
    "online media": "digital media",
    "hospitality": "hospitality tech",
    "research": "applied research",
    "consumer services": "consumer services tech",
    "entertainment": "media & entertainment tech",
    "apparel & fashion": "fashion-tech",
    "apparel and fashion": "fashion-tech",
    "farming": "agri-tech",
    "medical practice": "health-tech",
    "warehousing": "logistics tech",
    "accounting": "fintech",
    "consumer goods": "consumer products tech",
    "wholesale": "supply chain tech",
}

SUBJECT_BASE = "Internship application — Naman Chachan"

BODY_TEMPLATE = """Hi {first_name},

I came across the internship opportunity at {company_name} and wanted to reach out.

I'm a final-year CS student at MIT-WPU, currently interning at Oracle Financial
Services on Python automation and API work, wrapping up late July. Most of my own
projects are on the AI/backend side — I built a FastAPI chatbot that routes across
two LLM APIs with reranking to cut wrong answers by ~40%, a from-scratch testing
framework for LLM agents and tool-calling, and a Node.js + Express REST API backed
by MongoDB.
{focus_line}
I'm based in Pune, open to Bangalore or remote, and can start right after my Oracle
internship wraps in late July.

Resume: https://drive.google.com/file/d/1jV1S6E-b-3DPveNvQATKj1BnxHFnbJU9/view?usp=sharing
LinkedIn: https://www.linkedin.com/in/naman-chachan-903bb9277/

Would love to talk if it seems like a fit.
Thanks,
Naman Chachan
+91-8302862835
"""


def focus_area_for_niche(niche: str) -> Optional[str]:
    return NICHE_FOCUS_AREA.get((niche or "").strip().lower())


def load_custom_notes(path: str) -> dict[int, str]:
    """Load contact_id -> note overrides from custom_note.csv, if present."""
    notes: dict[int, str] = {}
    if not path or not os.path.exists(path):
        return notes
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                cid = int(row["contact_id"])
            except (KeyError, ValueError):
                logger.warning("Skipping malformed row in %s: %s", path, row)
                continue
            note = (row.get("note") or "").strip()
            if note:
                notes[cid] = note
    return notes


def render_email(
    *,
    name: str,
    company_name: str,
    company_niche: str,
    agency: bool,
    custom_note: Optional[str] = None,
) -> tuple[str, str, bool]:
    """Render (subject, body, used_focus_line) for a contact.

    Priority for the focus-area sentence:
      1. custom_note override (verbatim line, if provided)
      2. auto focus-area sentence from the niche map (unless agency=True)
      3. dropped entirely
    """
    first_name = name.strip().split()[0] if name.strip() else "there"

    if custom_note:
        focus_line = f"\n{custom_note.strip()}\n"
        used_focus = True
    elif not agency:
        focus_area = focus_area_for_niche(company_niche)
        if focus_area:
            focus_line = (
                f"\n{company_name}'s work in {focus_area} is exactly the kind of "
                f"problem space I want to go deeper on.\n"
            )
            used_focus = True
        else:
            focus_line = ""
            used_focus = False
    else:
        focus_line = ""
        used_focus = False

    body = BODY_TEMPLATE.format(first_name=first_name, company_name=company_name, focus_line=focus_line)
    subject = f"{SUBJECT_BASE} - {company_name}"
    return subject, body, used_focus
