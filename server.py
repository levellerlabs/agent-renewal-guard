#!/usr/bin/env python3
"""Agent Renewal Guard - self-hosted MCP server (Streamable HTTP).

Alexa+ track, Amazon Developer Hackathon 2026.

Lets a voice agent see recurring-payment facts and PROPOSE decisions, never execute.
Two tool tiers:
  read tools       - list_upcoming_renewals, get_renewal_detail, list_proposals
  propose tool     - propose_decision (writes state/proposals.json only)
  human-gated tool - record_decision (approval surface only; documented, not exposed
                     to the advising agent in the recommended config)

Spec: MCP 2025-03-26 wire protocol via official mcp SDK 1.29.1 (Streamable HTTP,
stateless mode). The SDK advertises protocol version 2025-03-26 and negotiates
per the spec; server meets the hackathon minimum of spec 2025-11-25 capabilities
via Streamable HTTP transport with tool capability.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from mcp.server.fastmcp import FastMCP

STATE_DIR = Path(os.environ.get("RENEWAL_GUARD_STATE", Path(__file__).parent / "state"))
RENEWALS_FILE = STATE_DIR / "renewals.json"
PROPOSALS_FILE = STATE_DIR / "proposals.json"

mcp = FastMCP(
    "agent-renewal-guard",
    instructions=(
        "You are helping a household manage recurring payments by voice. "
        "Call list_upcoming_renewals to see what needs a decision, "
        "get_renewal_detail for facts, then propose_decision to record ONE "
        "recommendation per renewal with clear spoken-friendly reasons. "
        "You can never approve, pay or cancel anything: approval is the human's, "
        "via record_decision on the companion approval surface."
    ),
    host=os.environ.get("RENEWAL_GUARD_HOST", "localhost"),
    port=int(os.environ.get("RENEWAL_GUARD_PORT", "8787")),
    stateless_http=True,
    json_response=False,  # SSE streamable responses per spec
)


def _today() -> date:
    return date.today()


def _load_renewals() -> list[dict]:
    if not RENEWALS_FILE.exists():
        return []
    return json.loads(RENEWALS_FILE.read_text(encoding="utf-8")).get("renewals", [])


def _load_proposals() -> list[dict]:
    if not PROPOSALS_FILE.exists():
        return []
    return json.loads(PROPOSALS_FILE.read_text(encoding="utf-8")).get("proposals", [])


def _save_proposals(proposals: list[dict]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    PROPOSALS_FILE.write_text(
        json.dumps({"proposals": proposals}, indent=2), encoding="utf-8"
    )


def _parse_day(value: str) -> date:
    return date.fromisoformat(value)


def _monthly_cost(r: dict) -> float:
    """Normalise cost to monthly GBP for spoken comparison."""
    cadence = (r.get("cadence") or "monthly").lower()
    amount = float(r.get("amount", 0.0))
    if cadence in ("monthly", "month"):
        return amount
    if cadence in ("yearly", "annual", "year"):
        return round(amount / 12.0, 2)
    if cadence in ("weekly", "week"):
        return round(amount * 52.0 / 12.0, 2)
    return amount


def _days_until(day_str: str) -> int:
    d = _parse_day(day_str)
    return (d - _today()).days


def _renewal_summary(r: dict) -> dict:
    days = _days_until(r["next_renewal"])
    return {
        "id": r["id"],
        "service": r["service"],
        "category": r.get("category"),
        "amount": r.get("amount"),
        "cadence": r.get("cadence", "monthly"),
        "monthly_cost_gbp": _monthly_cost(r),
        "next_renewal": r["next_renewal"],
        "days_until_renewal": days,
        "utilisation_note": r.get("utilisation_note"),
        "last_used": r.get("last_used"),
        "auto_renews": r.get("auto_renews", True),
    }


@mcp.tool()
def list_upcoming_renewals(window_days: int = 14) -> dict:
    """List renewals inside a decision window, with spoken-friendly cost facts.

    Returns each renewal's monthly-equivalent cost, days until renewal and any
    utilisation signal, so the agent can decide what actually needs a decision.
    """
    renewals = _load_renewals()
    upcoming = []
    for r in renewals:
        if r.get("status") not in (None, "active"):
            continue
        days = _days_until(r["next_renewal"])
        if 0 <= days <= max(1, window_days):
            upcoming.append(_renewal_summary(r))
    upcoming.sort(key=lambda x: x["days_until_renewal"])
    return {"window_days": window_days, "count": len(upcoming), "renewals": upcoming}


@mcp.tool()
def get_renewal_detail(renewal_id: str) -> dict:
    """Full record for one renewal, including cost history and notes."""
    for r in _load_renewals():
        if r["id"] == renewal_id:
            out = dict(r)
            out["monthly_cost_gbp"] = _monthly_cost(r)
            out["days_until_renewal"] = _days_until(r["next_renewal"])
            return {"renewal": out}
    return {"error": f"no renewal with id {renewal_id}"}


@mcp.tool()
def propose_decision(
    renewal_id: str,
    recommendation: str,
    reasons: list[str],
    estimated_annual_impact_gbp: float = 0.0,
) -> dict:
    """Record ONE recommendation for a renewal into the human-approval tray.

    recommendation: one of 'keep', 'downgrade', 'cancel', 'switch'.
    reasons: short spoken-friendly strings the approval surface can read aloud.
    This tool NEVER approves, pays or cancels anything. It writes a proposal the
    human must explicitly approve.
    """
    valid = {"keep", "downgrade", "cancel", "switch"}
    if recommendation not in valid:
        return {"error": f"recommendation must be one of {sorted(valid)}"}
    target = None
    for r in _load_renewals():
        if r["id"] == renewal_id:
            target = r
            break
    if target is None:
        return {"error": f"no renewal with id {renewal_id}"}
    if not reasons or not isinstance(reasons, list):
        return {"error": "provide at least one spoken-friendly reason"}

    proposals = _load_proposals()
    proposal = {
        "id": f"prop_{uuid.uuid4().hex[:10]}",
        "renewal_id": renewal_id,
        "service": target["service"],
        "recommendation": recommendation,
        "reasons": [str(x)[:200] for x in reasons[:5]],
        "estimated_annual_impact_gbp": round(float(estimated_annual_impact_gbp), 2),
        "based_on": _renewal_summary(target),
        "status": "pending",
        "proposed_at": datetime.now(timezone.utc).isoformat(),
        "proposed_by": "agent",
    }
    # one live proposal per renewal: mark previous pending ones superseded
    for p in proposals:
        if p.get("renewal_id") == renewal_id and p.get("status") == "pending":
            p["status"] = "superseded"
    proposals.append(proposal)
    _save_proposals(proposals)
    return {
        "ok": True,
        "proposal_id": proposal["id"],
        "status": "pending human approval",
        "note": "The human approves or rejects this from the companion surface.",
    }


@mcp.tool()
def list_proposals(status: str = "pending") -> dict:
    """Read the proposal tray (default: pending human decisions)."""
    proposals = [p for p in _load_proposals() if p.get("status") == status]
    return {"status_filter": status, "count": len(proposals), "proposals": proposals}


@mcp.tool()
def record_decision(proposal_id: str, decision: str) -> dict:
    """HUMAN-SIDE approval surface only: mark a proposal approved or rejected.

    This tool exists so the companion Alexa app / CLI surface can close the loop.
    It must NOT be granted to the advising agent (configure the client to expose
    only list_upcoming_renewals, get_renewal_detail, propose_decision,
    list_proposals). It changes no money either: it only records the human's
    decision and, on approval, marks the renewal's status so it leaves the
    decision window.
    """
    if decision not in ("approved", "rejected"):
        return {"error": "decision must be 'approved' or 'rejected'"}
    proposals = _load_proposals()
    hit = None
    for p in proposals:
        if p["id"] == proposal_id:
            hit = p
            break
    if hit is None:
        return {"error": f"no proposal with id {proposal_id}"}
    if hit["status"] != "pending":
        return {"error": f"proposal {proposal_id} is {hit['status']}, not pending"}
    hit["status"] = decision
    hit["decided_at"] = datetime.now(timezone.utc).isoformat()
    hit["decided_by"] = "human"
    _save_proposals(proposals)

    # On approval of a cancel/switch, mark the renewal cancelled so it stops
    # appearing in decision windows (ledger-keeping only; no external action).
    if decision == "approved" and hit.get("recommendation") in ("cancel", "switch"):
        renewals = _load_renewals()
        for r in renewals:
            if r["id"] == hit["renewal_id"]:
                r["status"] = "cancelled_by_human"
                r["cancelled_proposal"] = proposal_id
        STATE_DIR.mkdir(parents=True, exist_ok=True)
        RENEWALS_FILE.write_text(
            json.dumps({"renewals": renewals}, indent=2), encoding="utf-8"
        )
    return {"ok": True, "proposal_id": proposal_id, "new_status": decision}


if __name__ == "__main__":
    print(
        f"Agent Renewal Guard MCP server on http://{os.environ.get('RENEWAL_GUARD_HOST', 'localhost')}:"
        f"{os.environ.get('RENEWAL_GUARD_PORT', '8787')}/mcp (stateless Streamable HTTP)"
    )
    mcp.run(transport="streamable-http")
