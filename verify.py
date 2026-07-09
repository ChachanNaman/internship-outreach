"""Abstract API "Email Reputation" integration.

Free tier: 100 requests/month, instant self-serve API key, no card.
Sign up: https://app.abstractapi.com (Email Reputation product)
Docs: https://docs.abstractapi.com/api/email-reputation

Note: Abstract API issues a separate key per product -- the "Email
Reputation" key (used here) is different from the older "Email Validation"
product's key, and new accounts may only have Email Reputation available.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

logger = logging.getLogger("outreach")

ABSTRACT_URL = "https://emailreputation.abstractapi.com/v1/"


class QuotaExceededError(RuntimeError):
    """Raised when the Abstract API signals auth/quota failure."""


def verify_one(email: str, api_key: str, timeout: int = 10) -> dict:
    """Call the Abstract API for a single email. Returns the parsed JSON response.

    Raises QuotaExceededError on HTTP 401/402/429 (bad key or monthly quota
    exhausted), so callers can stop burning further attempts this run.
    """
    resp = requests.get(ABSTRACT_URL, params={"api_key": api_key, "email": email}, timeout=timeout)
    if resp.status_code in (401, 402, 429):
        raise QuotaExceededError(
            f"Abstract API returned HTTP {resp.status_code} for {email} "
            "(likely invalid API key or monthly quota exhausted)."
        )
    resp.raise_for_status()
    return resp.json()


def is_valid_and_safe(result: dict) -> bool:
    deliverability = result.get("email_deliverability") or {}
    status = str(deliverability.get("status", "")).strip().lower()
    smtp_valid = bool(deliverability.get("is_smtp_valid"))
    mx_valid = bool(deliverability.get("is_mx_valid"))

    quality = result.get("email_quality") or {}
    disposable = bool(quality.get("is_disposable"))

    risk = result.get("email_risk") or {}
    address_risk_high = str(risk.get("address_risk_status", "")).strip().lower() == "high"

    return status == "deliverable" and smtp_valid and mx_valid and not disposable and not address_risk_high


def verify_candidates(
    candidates: list[str], api_key: str
) -> tuple[Optional[str], str]:
    """Try each candidate in order; stop at the first deliverable + SMTP/MX-valid,
    non-disposable, low-risk result.

    Returns (verified_email_or_None, verify_status) where verify_status is one of:
    'valid', 'needs_manual_check'.
    """
    if not candidates:
        return None, "needs_manual_check"

    for candidate in candidates:
        try:
            result = verify_one(candidate, api_key)
        except QuotaExceededError:
            logger.warning("Abstract API quota/auth error while verifying %s", candidate)
            raise
        except requests.RequestException as exc:
            logger.warning("Abstract API request failed for %s: %s", candidate, exc)
            continue

        deliverability = result.get("email_deliverability") or {}
        logger.info(
            "Verify attempt: %s -> status=%s smtp_valid=%s mx_valid=%s",
            candidate,
            deliverability.get("status"),
            deliverability.get("is_smtp_valid"),
            deliverability.get("is_mx_valid"),
        )
        if is_valid_and_safe(result):
            return candidate, "valid"

    return None, "needs_manual_check"
