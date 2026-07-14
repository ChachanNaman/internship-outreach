"""Generate candidate email addresses from a person's name and company domain."""

from __future__ import annotations

import re
from typing import Optional
##

def _clean(part: str) -> str:
    return re.sub(r"[^a-z0-9]", "", part.lower())


def name_parts(full_name: str) -> tuple[str, str]:
    """Return (first, last) cleaned to bare alnum lowercase. last may be ''."""
    tokens = [t for t in re.split(r"\s+", full_name.strip()) if t]
    if not tokens:
        return "", ""
    first = _clean(tokens[0])
    last = _clean(tokens[-1]) if len(tokens) > 1 else ""
    return first, last

##
def generate_candidates(full_name: str, domain: Optional[str]) -> list[str]:
    """Build candidate emails in priority order:

    first.last@domain, first@domain, f.last@domain, firstlast@domain

    Duplicates (e.g. single-token names) are dropped while preserving order.
    Returns [] if there's no usable domain or name.
    """
    if not domain:
        return []
    first, last = name_parts(full_name)
    if not first:
        return []

    ordered = []
    if last:
        ordered.append(f"{first}.{last}@{domain}")
    ordered.append(f"{first}@{domain}")
    if last:
        ordered.append(f"{first[0]}.{last}@{domain}")
        ordered.append(f"{first}{last}@{domain}")

    seen = set()
    deduped = []
    for c in ordered:
        if c not in seen:
            seen.add(c)
            deduped.append(c)
    return deduped
