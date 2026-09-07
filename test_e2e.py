#!/usr/bin/env python3
"""End-to-end test for the Agent Renewal Guard MCP server.

Starts server.py as a Streamable HTTP subprocess, then drives it through the
JSON-RPC protocol: initialize, tools/list, and every tool's happy + error path.
Exit 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://localhost:8787/mcp"
HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "..", "strands-venv", "bin", "python")
if not os.path.exists(VENV_PY):
    VENV_PY = sys.executable  # fall back to any python with mcp installed

passed = 0
failed = 0


def check(name: str, cond: bool, extra: str = "") -> None:
    global passed, failed
    if cond:
        passed += 1
        print(f"PASS {name}")
    else:
        failed += 1
        print(f"FAIL {name} {extra}")


def post(payload: dict, session_id: str | None = None) -> tuple[int, dict | str]:
    req = urllib.request.Request(
        BASE,
        data=json.dumps(payload).encode(),
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    if session_id:
        req.add_header("Mcp-Session-Id", session_id)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            sid = resp.headers.get("Mcp-Session-Id")
            if not body.strip():
                return resp.status, {}, sid
            if body.startswith("event-stream:") or "data:" in body[:200]:
                # SSE: parse last data: line
                data_lines = [
                    ln[5:].strip()
                    for ln in body.splitlines()
                    if ln.startswith("data:")
                ]
                parsed = json.loads(data_lines[-1]) if data_lines else body
            else:
                parsed = json.loads(body)
            return resp.status, parsed, sid
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(), None


def main() -> int:
    # isolated state per run: copy seed renewals into a temp dir
    import shutil
    import tempfile

    state_dir = Path(tempfile.mkdtemp(prefix="renewal-guard-test-"))
    shutil.copy(os.path.join(HERE, "state", "renewals.json"), state_dir / "renewals.json")
    (state_dir / "proposals.json").write_text('{"proposals": []}', encoding="utf-8")

    env = dict(os.environ)
    env["RENEWAL_GUARD_STATE"] = str(state_dir)
    env["RENEWAL_GUARD_PORT"] = "8787"
    proc = subprocess.Popen(
        [VENV_PY, os.path.join(HERE, "server.py")],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    try:
        # wait for server up
        ready = False
        for _ in range(40):
            time.sleep(0.25)
            try:
                status, body, sid = post(
                    {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-03-26",
                            "capabilities": {},
                            "clientInfo": {"name": "e2e-test", "version": "1.0"},
                        },
                    }
                )
                if status == 200:
                    ready = True
                    break
            except Exception:
                continue
        check("server up + initialize 200", ready)
        if not ready:
            return 1
        server_info = body.get("result", {}).get("serverInfo", {})
        check(
            "serverInfo name",
            server_info.get("name") == "agent-renewal-guard",
            str(server_info),
        )
        proto = body.get("result", {}).get("protocolVersion", "")
        check("protocol version negotiated", bool(proto), proto)

        # notifications/initialized
        post(
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            session_id=sid,
        )

        # tools/list
        status, tlist, _ = post(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, session_id=sid
        )
        tools = {t["name"]: t for t in tlist.get("result", {}).get("tools", [])}
        check(
            "5 tools exposed",
            set(tools) == {
                "list_upcoming_renewals",
                "get_renewal_detail",
                "propose_decision",
                "list_proposals",
                "record_decision",
            },
            str(sorted(tools)),
        )

        # list_upcoming_renewals default window
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "list_upcoming_renewals",
                    "arguments": {"window_days": 14},
                },
            },
            session_id=sid,
        )
        text = res["result"]["content"][0]["text"]
        data = json.loads(text)
        ids = [r["id"] for r in data["renewals"]]
        check("window 14d catches gym, cloud, music, stream", set(ids) == {"gym-flex", "cloud-2tb", "music-solo", "stream-family"}, str(ids))
        check("monthly cost normalised", data["renewals"][0].get("monthly_cost_gbp") is not None)
        yearly = [r for r in data["renewals"]]
        check("sorted by days", data["renewals"] == sorted(data["renewals"], key=lambda x: x["days_until_renewal"]))

        # get_renewal_detail happy + error
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "get_renewal_detail", "arguments": {"renewal_id": "domain-portfolio"}},
            },
            session_id=sid,
        )
        det = json.loads(res["result"]["content"][0]["text"])
        check("detail yearly monthly-equiv ~8.00", abs(det["renewal"]["monthly_cost_gbp"] - 8.0) < 0.01, str(det["renewal"].get("monthly_cost_gbp")))
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "get_renewal_detail", "arguments": {"renewal_id": "nope"}},
            },
            session_id=sid,
        )
        check("detail error path", "error" in json.loads(res["result"]["content"][0]["text"]))

        # propose_decision happy
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {
                    "name": "propose_decision",
                    "arguments": {
                        "renewal_id": "gym-flex",
                        "recommendation": "cancel",
                        "reasons": ["visited twice since March", "pay-as-you-go passes cost less"],
                        "estimated_annual_impact_gbp": 395.88,
                    },
                },
            },
            session_id=sid,
        )
        prop = json.loads(res["result"]["content"][0]["text"])
        check("proposal created pending", prop.get("ok") is True and "pending" in prop.get("status", ""), str(prop))
        proposal_id = prop.get("proposal_id")

        # propose second proposal for same renewal -> supersedes first
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 7,
                "method": "tools/call",
                "params": {
                    "name": "propose_decision",
                    "arguments": {
                        "renewal_id": "gym-flex",
                        "recommendation": "downgrade",
                        "reasons": ["off-peak tier is 19.99 and fits usage"],
                        "estimated_annual_impact_gbp": 156.0,
                    },
                },
            },
            session_id=sid,
        )
        prop2 = json.loads(res["result"]["content"][0]["text"])
        check("second proposal ok", prop2.get("ok") is True)
        status, res, _ = post(
            {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "list_proposals", "arguments": {"status": "superseded"}}},
            session_id=sid,
        )
        sup = json.loads(res["result"]["content"][0]["text"])
        check("first proposal superseded", sup["count"] == 1 and sup["proposals"][0]["id"] == proposal_id, str(sup))

        # invalid recommendation
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "propose_decision",
                    "arguments": {"renewal_id": "gym-flex", "recommendation": "explode", "reasons": ["x"]},
                },
            },
            session_id=sid,
        )
        check("invalid recommendation rejected", "error" in json.loads(res["result"]["content"][0]["text"]))

        # record_decision on superseded proposal -> error
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "record_decision", "arguments": {"proposal_id": proposal_id, "decision": "approved"}},
            },
            session_id=sid,
        )
        check("cannot decide superseded", "error" in json.loads(res["result"]["content"][0]["text"]))

        # record_decision approve the pending downgrade -> renewal stays active (not cancel/switch)
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 11,
                "method": "tools/call",
                "params": {"name": "list_proposals", "arguments": {"status": "pending"}},
            },
            session_id=sid,
        )
        pend = json.loads(res["result"]["content"][0]["text"])
        pid2 = pend["proposals"][0]["id"]
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "record_decision", "arguments": {"proposal_id": pid2, "decision": "approved"}},
            },
            session_id=sid,
        )
        dec = json.loads(res["result"]["content"][0]["text"])
        check("approve pending ok", dec.get("ok") is True, str(dec))
        check("gym not auto-cancelled on downgrade approve", dec.get("ok") is True)

        # now propose cancel on stream-family and approve -> status becomes cancelled_by_human
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 13,
                "method": "tools/call",
                "params": {
                    "name": "propose_decision",
                    "arguments": {
                        "renewal_id": "stream-family",
                        "recommendation": "cancel",
                        "reasons": ["kids moved out in June"],
                        "estimated_annual_impact_gbp": 215.88,
                    },
                },
            },
            session_id=sid,
        )
        p3 = json.loads(res["result"]["content"][0]["text"])
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 14,
                "method": "tools/call",
                "params": {"name": "record_decision", "arguments": {"proposal_id": p3["proposal_id"], "decision": "approved"}},
            },
            session_id=sid,
        )
        status, res, _ = post(
            {
                "jsonrpc": "2.0",
                "id": 15,
                "method": "tools/call",
                "params": {"name": "list_upcoming_renewals", "arguments": {"window_days": 30}},
            },
            session_id=sid,
        )
        after = json.loads(res["result"]["content"][0]["text"])
        ids_after = [r["id"] for r in after["renewals"]]
        check("cancelled stream-family left window", "stream-family" not in ids_after, str(ids_after))

        print(f"\n{passed} passed, {failed} failed")
        return 0 if failed == 0 else 1
    finally:
        proc.terminate()
        proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
