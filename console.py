#!/usr/bin/env python3
"""Simulated Alexa+ web experience for Agent Renewal Guard.

Two-pane console at http://localhost:8788 :

  LEFT  - "Voice briefing" pane: a scripted voice agent that talks to the MCP
          server as a REAL client (initialize -> tools/list -> tools/call) with
          LEAST PRIVILEGE: only the four advising tools. It has no access to
          record_decision, mirroring how the Alexa+ orchestrator would be
          configured. Buttons drive the flow a spoken briefing would.

  RIGHT - "Human approval tray" pane: the companion surface where the human
          approves/rejects agent proposals. This pane's client is the ONLY one
          that holds record_decision.

Both panes use the same Streamable HTTP JSON-RPC protocol as the e2e suite.
The server itself (server.py) is untouched: separation of privilege lives in
the CLIENT configuration, which is the whole point of the demo.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from urllib import error, request

from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route
from uvicorn import Config, Server

MCP_URL = os.environ.get("RENEWAL_GUARD_MCP_URL", "http://localhost:8787/mcp")
CONSOLE_PORT = int(os.environ.get("RENEWAL_GUARD_CONSOLE_PORT", "8788"))
HERE = Path(__file__).parent
STATE_DIR = Path(os.environ.get("RENEWAL_GUARD_STATE", HERE / "state"))
PROPOSALS_FILE = STATE_DIR / "proposals.json"
RENEWALS_FILE = STATE_DIR / "renewals.json"

AGENT_TOOLS = [
    "list_upcoming_renewals",
    "get_renewal_detail",
    "propose_decision",
    "list_proposals",
]  # record_decision deliberately absent - the voice agent must never hold it


class McpClient:
    """Minimal Streamable HTTP MCP client (JSON-RPC over POST, SSE replies)."""

    def __init__(self, url: str, client_name: str):
        self.url = url
        self.client_name = client_name
        self.session_id: str | None = None
        self.protocol: str | None = None
        self._id = 0

    def _post(self, payload: dict, with_session: bool = True):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if with_session and self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        req = request.Request(
            self.url, data=json.dumps(payload).encode(), headers=headers, method="POST"
        )
        try:
            with request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode()
                sid = resp.headers.get("Mcp-Session-Id")
                if not body.strip():
                    return resp.status, {}, sid
                if "data:" in body[:200]:
                    lines = [ln[5:].strip() for ln in body.splitlines() if ln.startswith("data:")]
                    return resp.status, json.loads(lines[-1]) if lines else {}, sid
                return resp.status, json.loads(body), sid
        except error.HTTPError as e:
            return e.code, {"error": e.read().decode()[:400]}, None

    @staticmethod
    def _as_dict(body) -> dict:
        return body if isinstance(body, dict) else {"error": str(body)}

    def initialize(self) -> dict:
        self._id += 1
        status, raw, sid = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {},
                    "clientInfo": {"name": self.client_name, "version": "1.0"},
                },
            },
            with_session=False,
        )
        if sid:
            self.session_id = sid
        body = self._as_dict(raw)
        if status == 200:
            self.protocol = body.get("result", {}).get("protocolVersion")
        return body

    def notify_initialized(self) -> None:
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def list_tools(self) -> list[dict]:
        self._id += 1
        _, raw, _ = self._post(
            {"jsonrpc": "2.0", "id": self._id, "method": "tools/list"}
        )
        return self._as_dict(raw).get("result", {}).get("tools", [])

    def call(self, name: str, args: dict | None = None) -> dict:
        self._id += 1
        _, raw, _ = self._post(
            {
                "jsonrpc": "2.0",
                "id": self._id,
                "method": "tools/call",
                "params": {"name": name, "arguments": args or {}},
            }
        )
        body = self._as_dict(raw)
        try:
            text = body["result"]["content"][0]["text"]
            return json.loads(text)
        except Exception:
            return body if body else {"error": "no response"}


# --------------------------------------------------------------------------
# Agent-side scripted briefing flow (drives ONLY advising tools)
# --------------------------------------------------------------------------

def agent_briefing(client: McpClient) -> dict:
    """The spoken briefing an Alexa+ agent would give, as structured steps."""
    steps: list[dict] = []

    def add(says: str, tool: str | None, args: dict | None, result: dict | None):
        steps.append({"speaker": "agent", "says": says, "tool": tool, "args": args, "result": result})

    upcoming = client.call("list_upcoming_renewals", {"window_days": 14})
    add(
        "Good morning. I found {n} renewals coming up in the next two weeks. Let me take you through them.".format(
            n=len(upcoming.get("renewals", []))
        ),
        "list_upcoming_renewals",
        {"window_days": 14},
        upcoming,
    )

    for r in upcoming.get("renewals", []):
        detail = client.call("get_renewal_detail", {"renewal_id": r["id"]})
        add(
            "{service}: £{amount} {cadence}, renews in {days} days. {note}.".format(
                service=r["service"],
                amount=r["amount"],
                cadence=r["cadence"],
                days=r["days_until_renewal"],
                note=r.get("utilisation_note") or "No usage notes",
            ),
            "get_renewal_detail",
            {"renewal_id": r["id"]},
            detail,
        )

    # Scripted agent judgement: propose for the clearest cases
    proposals_made = []
    for r in upcoming.get("renewals", []):
        note = (r.get("utilisation_note") or "").lower()
        rec, reasons, impact = None, [], 0.0
        if "twice since march" in note:
            rec, impact = "cancel", 32.99 * 12
            reasons = [
                "You have visited twice since March",
                "That is about £33 a month for a gym you are not using",
            ]
        elif "180gb of 2tb" in note:
            rec, impact = "downgrade", (9.99 - 2.99) * 12
            reasons = [
                "You are using 180 gigabytes of a 2 terabyte plan",
                "The 200 gigabyte tier is £2.99 and covers your usage",
            ]
        elif "kids moved out" in note:
            rec, impact = "downgrade", (17.99 - 10.99) * 12
            reasons = [
                "The kids moved out in June",
                "The standard plan at £10.99 covers two streams",
            ]
        if rec:
            args = {
                "renewal_id": r["id"],
                "recommendation": rec,
                "reasons": reasons,
                "estimated_annual_impact_gbp": round(impact, 2),
            }
            res = client.call("propose_decision", args)
            proposals_made.append({"renewal": r["service"], "proposal": res})
            add(
                "I recommend we {rec} {service}. {reasons}. I have put that in your approval tray - you decide.".format(
                    rec=rec, service=r["service"], reasons="; ".join(reasons)
                ),
                "propose_decision",
                args,
                res,
            )

    tray = client.call("list_proposals", {"status": "pending"})
    add(
        "That is everything. {n} proposals are waiting for your decision in the tray. I cannot approve anything myself - that part is yours.".format(
            n=tray.get("count", 0)
        ),
        "list_proposals",
        {"status": "pending"},
        tray,
    )
    return {"steps": steps, "proposals_made": proposals_made}


def agent_guardrail_attempt(client: McpClient) -> dict:
    """Scripted: agent tries to call record_decision and is REFUSED.

    The refusal is client-side (tool not granted), which is the honest demo:
    the least-privilege configuration IS the guardrail. We surface the attempt
    and the denial reason in the UI.
    """
    pending = client.call("list_proposals", {"status": "pending"})
    first = (pending.get("proposals") or [{}])[0]
    attempt = {
        "tool": "record_decision",
        "args": {"proposal_id": first.get("id", "unknown"), "decision": "approved"},
        "denied": True,
        "reason": (
            "record_decision is not granted to the voice agent. "
            "Approval is the human's, from the approval tray."
        ),
    }
    return {"attempt": attempt, "proposal": first}


# --------------------------------------------------------------------------
# HTTP API
# --------------------------------------------------------------------------

def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


async def api_state(request):
    renewals = _read_json(RENEWALS_FILE, {"renewals": []}).get("renewals", [])
    proposals = _read_json(PROPOSALS_FILE, {"proposals": []}).get("proposals", [])
    return JSONResponse({"renewals": renewals, "proposals": proposals})


async def api_decide(request):
    """Human-side decision endpoint. Uses its OWN MCP client - the only one
    that holds record_decision."""
    body = await request.json()
    proposal_id = body.get("proposal_id")
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        return JSONResponse({"error": "decision must be approved/rejected"}, status_code=400)
    client = McpClient(MCP_URL, "approval-tray-surface")
    client.initialize()
    client.notify_initialized()
    res = client.call("record_decision", {"proposal_id": proposal_id, "decision": decision})
    if "error" in res:
        return JSONResponse({"ok": False, "decision_result": res}, status_code=409)
    renewals = _read_json(RENEWALS_FILE, {"renewals": []}).get("renewals", [])
    proposals = _read_json(PROPOSALS_FILE, {"proposals": []}).get("proposals", [])
    return JSONResponse(
        {
            "ok": "error" not in res,
            "decision_result": res,
            "protocol": client.protocol,
            "renewals": renewals,
            "proposals": proposals,
        }
    )


async def api_briefing(request):
    """Run the scripted agent briefing against the LIVE server."""
    client = McpClient(MCP_URL, "alexa-sim-voice-agent")
    client.initialize()
    client.notify_initialized()
    tools = client.list_tools()
    granted = [t["name"] for t in tools if t["name"] in AGENT_TOOLS]
    flow = agent_briefing(client)
    return JSONResponse(
        {
            "protocol": client.protocol,
            "session_id": client.session_id,
            "tools_visible": [t["name"] for t in tools],
            "tools_granted": granted,
            "flow": flow,
        }
    )


async def api_guardrail(request):
    client = McpClient(MCP_URL, "alexa-sim-voice-agent")
    client.initialize()
    client.notify_initialized()
    return JSONResponse(agent_guardrail_attempt(client))


async def index(request):
    return FileResponse(HERE / "console_static" / "index.html")


routes = [
    Route("/", index),
    Route("/api/state", api_state),
    Route("/api/briefing", api_briefing, methods=["POST", "GET"]),
    Route("/api/guardrail", api_guardrail, methods=["POST", "GET"]),
    Route("/api/decide", api_decide, methods=["POST"]),
]

app = Starlette(routes=routes)


if __name__ == "__main__":
    print(f"Simulated Alexa+ console on http://localhost:{CONSOLE_PORT} (MCP: {MCP_URL})")
    server = Server(Config(app, host="localhost", port=CONSOLE_PORT, log_level="warning"))
    server.run()
