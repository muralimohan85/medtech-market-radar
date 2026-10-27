"""Analyst approval bound to the exact draft, and the publish gate.

The approval is an HMAC over the draft's content hash, the analyst's name and the
decision. Edit one word of the draft after approval and the gate refuses to publish
it. Drafts that fail the fact-checker cannot be approved at all.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import datetime, timezone


class PublishRefused(PermissionError):
    pass


def signature(secret: str, content_hash: str, name: str, decision: str) -> str:
    return hmac.new(secret.encode(), f"{content_hash}|{name}|{decision}".encode(), hashlib.sha256).hexdigest()


def approve(content_hash: str, name: str, secret: str, note: str = "", decision: str = "approved") -> dict:
    if not name.strip():
        raise PublishRefused("an approval needs the analyst's name")
    return {"name": name, "decision": decision, "hash": content_hash, "note": note,
            "signature": signature(secret, content_hash, name, decision),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def verify(approval: dict, content_hash: str, secret: str, checks: dict) -> None:
    if checks.get("blocked"):
        raise PublishRefused("the fact-checker found problems that must be fixed first")
    if not approval or approval.get("decision") != "approved":
        raise PublishRefused("not approved")
    if approval.get("hash") != content_hash:
        raise PublishRefused("the draft changed after it was approved")
    if not hmac.compare_digest(approval.get("signature", ""), signature(secret, content_hash, approval["name"], "approved")):
        raise PublishRefused("invalid approval signature")
