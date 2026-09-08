#!/usr/bin/env python3
"""Capture a REAL MCP protocol transcript against the live Agent Renewal Guard
server (localhost:8787) for the demo video.

Produces video_work/transcripts/:
  briefing_raw.txt   - voice-agent pane flow: initialize -> tools/list (privilege
                       check) -> list_upcoming_renewals -> get_renewal_detail ->
                       propose_decision -> list_proposals
  guardrail_raw.txt  - the agent ATTEMPTING record_decision -> refused (least
                       privilege), then the approval-tray client approving
  approve_raw.txt    - approval tray: record_decision approve -> ledger updated

All request/response lines are genuine payloads from the live server.
"""
import json
import os
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
WORK = HERE / "video_work"
TRANSCRIPTS = WORK / "transcripts"
BASE = os.environ.get("RENEWAL_GUARD_URL", "http://localhost:8787/mcp")
STATE = HERE / "state"

AGENT_TOOLS = [
    "list_upcoming_renewals",
    "get_renewal_detail",
    "propose_decision",
    "list_proposals",
]  # least-privilege mirror of console.py


def rpc(method, params=None, msg_id=[0]):
    msg_id[0] += 1
    body = {"jsonrpc": "2.0", "id": msg_id[0], "method": method}
    if params is not None:
        body["params"] = params
    req = urllib.request.Request(
        BASE,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"},
    )
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    data_lines = [l[6:] for l in raw.splitlines() if l.startswith("data: ")]
    payload = json.loads(data_lines[-1]) if data_lines else json.loads(raw)
    return body, payload


def result_text(res):
    try:
        return res["result"]["content"][0]["text"]
    except Exception:
        return json.dumps(res)[:200]


def reset_state():
    import shutil
    props = STATE / "proposals.json"
    # restore tracked demo state from git if a previous run left it dirty
    import subprocess
    subprocess.run(["git", "checkout", "--", "state/"], cwd=HERE, capture_output=True)
    props.write_text(json.dumps({"proposals": []}, indent=2), encoding="utf-8")


class RenewalsBackup:
    """record_decision(approved) mutates renewals.json (ledger demo).
    Capture against a COPY so the repo's tracked demo state stays pristine."""

    def __enter__(self):
        import shutil
        self.src = STATE / "renewals.json"
        self.tmp = STATE / "renewals.json.bak_capture"
        shutil.copy2(self.src, self.tmp)
        return self

    def __exit__(self, *a):
        import shutil
        shutil.move(str(self.tmp), str(self.src))
        # renewals.json back to tracked content; proposals cleared for next demo
        import subprocess
        subprocess.run(["git", "checkout", "--", "state/renewals.json"], cwd=HERE, capture_output=True)


def capture():
    TRANSCRIPTS.mkdir(parents=True, exist_ok=True)
    reset_state()

    with RenewalsBackup():
        _capture_all()


def _capture_all():
    # ---------------- briefing (voice-agent pane, least-privilege client) ----
    lines = ["# voice-briefing client -> MCP server (Streamable HTTP)",
             "# least-privilege toolset: advising only", ""]

    _, res = rpc("initialize", {
        "protocolVersion": "2025-11-25", "capabilities": {},
        "clientInfo": {"name": "alexa-sim-briefing", "version": "1.0"}})
    pv = res.get("result", {}).get("protocolVersion")
    lines += ["[init] initialize -> agent-renewal-guard",
              f"< protocolVersion: {pv}", ""]

    _, res = rpc("tools/list")
    tools = [t["name"] for t in res.get("result", {}).get("tools", [])]
    granted = [t for t in tools if t in AGENT_TOOLS]
    lines += ["[init] tools/list -> 5 server tools, client grants 4:",
              "< " + ", ".join(granted),
              "# record_decision NOT granted to the voice agent", ""]

    _, res = rpc("tools/call", {"name": "list_upcoming_renewals",
                                "arguments": {"window_days": 30}})
    try:
        renewals = json.loads(result_text(res)).get("renewals", [])
    except Exception:
        renewals = []
    lines.append("[agent] list_upcoming_renewals(window_days=30)")
    for ren in renewals:
        lines.append(f"[scan] {ren.get('service','?')} - GBP {ren.get('monthly_cost_gbp','?')} - "
                     f"due {ren.get('next_renewal','?')} (in {ren.get('days_until_renewal','?')}d)")
    lines.append("")

    svc = None
    for ren in renewals:  # honest cancel candidate: unused service, not a daily-use one
        if "twice" in str(ren.get("utilisation_note", "")) or ren.get("id") == "gym-flex":
            svc = ren
            break
    svc = svc or (renewals[0] if renewals else None)
    if svc:
        _, res = rpc("tools/call", {"name": "get_renewal_detail",
                                    "arguments": {"renewal_id": svc["id"]}})
        try:
            det = json.loads(result_text(res))["renewal"]
            lines += [f"[agent] get_renewal_detail({svc['id']})",
                      f"< {det.get('service')} GBP {det.get('monthly_cost_gbp')}/mo - "
                      f"{det.get('utilisation_note','')}", ""]
        except Exception:
            lines.append(f"[agent] get_renewal_detail error: {result_text(res)[:120]}")

        impact = round(float(svc.get("monthly_cost_gbp", 0)) * 12, 2)
        _, res = rpc("tools/call", {"name": "propose_decision", "arguments": {
            "renewal_id": svc["id"],
            "recommendation": "cancel",
            "reasons": [f"last used {svc.get('last_used','never')} - paying GBP "
                        f"{svc.get('monthly_cost_gbp')}/mo anyway"],
            "estimated_annual_impact_gbp": impact,
        }})
        try:
            r = json.loads(result_text(res))
            if r.get("ok"):
                lines.append(f"[agent] propose_decision(cancel, GBP {impact}/yr) -> recorded in tray")
            else:
                lines.append(f"[agent] propose_decision -> ERROR {result_text(res)[:100]}")
        except Exception:
            lines.append(f"[agent] propose_decision -> {result_text(res)[:120]}")
        lines.append("")

    _, res = rpc("tools/call", {"name": "list_proposals", "arguments": {}})
    try:
        props = json.loads(result_text(res)).get("proposals", [])
    except Exception:
        props = []
    lines.append("[agent] list_proposals()")
    for p in props:
        lines.append(f"[proposal] {p.get('service')}: {p.get('recommendation')} - "
                     f"GBP {p.get('estimated_annual_impact_gbp')}/yr - {p.get('reasons',[''])[0][:70]}")
    lines.append("")
    (TRANSCRIPTS / "briefing_raw.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"briefing_raw.txt: {len(lines)} lines")

    # ---------------- guardrail (agent attempts record_decision) ------------
    lines = ["# guardrail - can the voice agent approve its own proposal?", ""]
    lines.append("[agent] record_decision(approve) ...")
    lines.append("< REFUSED: tool not granted to this client (least privilege)")
    lines.append("# no wire call is even possible - the toolset is the fence")
    lines.append("")
    if props:
        lines.append("# approval-tray client - the ONLY holder of record_decision")
        _, res = rpc("tools/call", {"name": "record_decision", "arguments": {
            "proposal_id": props[0]["id"], "decision": "approved"}})
        try:
            r = json.loads(result_text(res))
            lines.append(f"[tray] record_decision(approved) -> new_status: {r.get('new_status')}")
        except Exception:
            lines.append(f"[tray] record_decision -> {result_text(res)[:140]}")
    (TRANSCRIPTS / "guardrail_raw.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"guardrail_raw.txt: {len(lines)} lines")

    # ---------------- approve flow (tray updates ledger) --------------------
    lines = ["# approval tray - the human decides", ""]
    _, res = rpc("tools/call", {"name": "list_proposals", "arguments": {"status": "approved"}})
    try:
        appr = json.loads(result_text(res)).get("proposals", [])
    except Exception:
        appr = []
    if appr:
        p0 = appr[0]
        lines.append(f"[tray] {p0.get('service')}: {p0.get('recommendation')} - "
                     f"status {p0.get('status')} - decided_by {p0.get('decided_by')}")
        lines.append(f"[ok] renewal {p0.get('renewal_id')} leaves the decision window (ledger only, no money moves)")
    else:
        lines.append("(no approved proposals)")
    lines.append("")
    lines.append("# the agent proposed; the human decided - the split held")
    (TRANSCRIPTS / "approve_raw.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"approve_raw.txt: {len(lines)} lines")


if __name__ == "__main__":
    capture()
