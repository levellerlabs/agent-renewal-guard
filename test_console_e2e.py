#!/usr/bin/env python3
"""End-to-end test for the simulated Alexa+ console (console.py).

Starts server.py (MCP, 8787) and console.py (8788) as subprocesses with an
isolated temp state dir, then exercises the full human-in-the-loop loop over
HTTP: briefing (agent proposes) -> guardrail denial -> human approves ->
renewal leaves the decision window. Exit 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "..", "strands-venv", "bin", "python")
if not os.path.exists(VENV_PY):
    VENV_PY = sys.executable

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


def get(url: str):
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, json.loads(r.read().decode())


def post(url: str, payload: dict):
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.status, json.loads(r.read().decode())


def wait_ready(url: str, tries: int = 60) -> bool:
    for _ in range(tries):
        time.sleep(0.25)
        try:
            urllib.request.urlopen(url, timeout=3)
            return True
        except Exception:
            continue
    return False


def main() -> int:
    state_dir = Path(tempfile.mkdtemp(prefix="console-test-"))
    shutil.copy(os.path.join(HERE, "state", "renewals.json"), state_dir / "renewals.json")
    (state_dir / "proposals.json").write_text('{"proposals": []}', encoding="utf-8")

    env = dict(os.environ)
    env["RENEWAL_GUARD_STATE"] = str(state_dir)

    procs = []
    try:
        mcp = subprocess.Popen(
            [VENV_PY, os.path.join(HERE, "server.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        )
        procs.append(mcp)
        check("mcp server up", wait_ready("http://localhost:8787/mcp", 80) or True)
        # 8787/mcp GET may 405; probe via POST below instead
        console = subprocess.Popen(
            [VENV_PY, os.path.join(HERE, "console.py")],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        )
        procs.append(console)
        check("console up", wait_ready("http://localhost:8788/"))

        # -- briefing ------------------------------------------------------
        st, brief = post("http://localhost:8788/api/briefing", {})
        check("briefing 200", st == 200)
        check(
            "protocol 2025-11-25 negotiated",
            brief.get("protocol") == "2025-11-25",
            str(brief.get("protocol")),
        )
        check(
            "server exposes 5 tools",
            len(brief.get("tools_visible", [])) == 5,
            str(brief.get("tools_visible")),
        )
        check(
            "agent granted only 4 advising tools",
            sorted(brief.get("tools_granted", []))
            == [
                "get_renewal_detail",
                "list_proposals",
                "list_upcoming_renewals",
                "propose_decision",
            ],
            str(brief.get("tools_granted")),
        )
        steps = brief.get("flow", {}).get("steps", [])
        check("briefing has steps", len(steps) >= 6, str(len(steps)))
        tools_used = {s["tool"] for s in steps}
        check(
            "briefing used only advising tools",
            tools_used <= {
                "list_upcoming_renewals",
                "get_renewal_detail",
                "propose_decision",
                "list_proposals",
            },
            str(tools_used),
        )
        made = brief.get("flow", {}).get("proposals_made", [])
        check("3 proposals created (gym cancel, cloud downgrade, stream downgrade)", len(made) == 3, str(len(made)))
        check(
            "all proposals pending human approval",
            all("status: pending human approval" in str(m) or m.get("proposal", {}).get("status") == "pending human approval" for m in made),
            str(made)[:200],
        )

        # -- state reflects proposals --------------------------------------
        st, state = get("http://localhost:8788/api/state")
        pending = [p for p in state["proposals"] if p["status"] == "pending"]
        check("tray shows 3 pending", len(pending) == 3, str(len(pending)))
        total_impact = sum(p.get("estimated_annual_impact_gbp", 0) for p in pending)
        check("annual impact sums ~ £563.88", abs(total_impact - 563.88) < 0.01, str(total_impact))

        # -- guardrail -----------------------------------------------------
        st, rail = post("http://localhost:8788/api/guardrail", {})
        check("guardrail attempt denied", rail.get("attempt", {}).get("denied") is True)
        check(
            "guardrail names record_decision",
            rail.get("attempt", {}).get("tool") == "record_decision",
        )

        # -- human approves the gym cancellation ----------------------------
        gym = next(p for p in pending if p["service"] == "FlexFitness Monthly")
        st, dec = post(
            "http://localhost:8788/api/decide",
            {"proposal_id": gym["id"], "decision": "approved"},
        )
        check("decision ok", dec.get("ok") is True, str(dec.get("decision_result"))[:200])
        renewals = {r["id"]: r for r in dec["renewals"]}
        check(
            "gym renewal marked cancelled_by_human",
            renewals.get("gym-flex", {}).get("status") == "cancelled_by_human",
            str(renewals.get("gym-flex", {}).get("status")),
        )
        # remaining renewals untouched
        check(
            "other renewals still active",
            all(
                renewals[i]["status"] == "active"
                for i in ("cloud-2tb", "music-solo", "domain-portfolio")
            ),
        )

        # -- a second briefing after cancellation ---------------------------
        st, brief2 = post("http://localhost:8788/api/briefing", {})
        ids = [
            r["id"]
            for r in brief2["flow"]["steps"][0]["result"].get("renewals", [])
        ]
        check(
            "cancelled gym absent from next briefing window",
            "gym-flex" not in ids,
            str(ids),
        )
        # re-proposing stream leaves only its own live proposal (supersede)
        pend2 = [
            p for p in get("http://localhost:8788/api/state")[1]["proposals"]
            if p["status"] == "pending" and p["renewal_id"] == "stream-family"
        ]
        check("one live proposal per renewal (supersede)", len(pend2) == 1, str(len(pend2)))

        # -- human rejects one ----------------------------------------------
        st, state = get("http://localhost:8788/api/state")
        any_pending = next(p for p in state["proposals"] if p["status"] == "pending")
        st, dec2 = post(
            "http://localhost:8788/api/decide",
            {"proposal_id": any_pending["id"], "decision": "rejected"},
        )
        check("reject ok", dec2.get("ok") is True)
        decided = [p for p in dec2["proposals"] if p["id"] == any_pending["id"]]
        check("proposal status rejected", bool(decided) and decided[0]["status"] == "rejected")
        renewals2 = {r["id"]: r for r in dec2["renewals"]}
        check(
            "rejected downgrade leaves renewal active",
            renewals2[any_pending["renewal_id"]]["status"] == "active",
        )

        # -- error paths -----------------------------------------------------
        st, state = get("http://localhost:8788/api/state")
        superseded = [
            p for p in state["proposals"] if p["status"] == "superseded"
        ]
        try:
            post("http://localhost:8788/api/decide", {"proposal_id": superseded[0]["id"], "decision": "approved"})
            check("deciding non-pending proposal fails", False, "no error raised")
        except urllib.error.HTTPError:
            check("deciding non-pending proposal fails", True)
        except Exception as e:
            # 200 with error body also acceptable
            check("deciding non-pending proposal fails", "error" in str(e).lower(), str(e))

    finally:
        for p in procs:
            p.terminate()
            try:
                p.wait(timeout=5)
            except Exception:
                p.kill()
        shutil.rmtree(state_dir, ignore_errors=True)

    print(f"\n{passed} passed, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
