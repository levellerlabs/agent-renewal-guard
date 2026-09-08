#!/usr/bin/env python3
"""Playwright verification of the simulated Alexa+ console against the LIVE app.

Starts both servers with isolated state, opens the console in a real browser,
clicks through briefing -> guardrail -> approve -> reject, and screenshots the
key states for the demo video. Exit 0 = all checks pass.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
VENV_PY = os.path.join(HERE, "..", "strands-venv", "bin", "python")
SHOTS = Path(HERE) / "shots"
SHOTS.mkdir(exist_ok=True)

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
    state_dir = Path(tempfile.mkdtemp(prefix="console-ui-test-"))
    shutil.copy(os.path.join(HERE, "state", "renewals.json"), state_dir / "renewals.json")
    (state_dir / "proposals.json").write_text('{"proposals": []}', encoding="utf-8")

    env = dict(os.environ)
    env["RENEWAL_GUARD_STATE"] = str(state_dir)

    procs = []
    with sync_playwright() as pw:
        try:
            mcp = subprocess.Popen(
                [VENV_PY, os.path.join(HERE, "server.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            )
            procs.append(mcp)
            console = subprocess.Popen(
                [VENV_PY, os.path.join(HERE, "console.py")],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
            )
            procs.append(console)
            check("servers up", wait_ready("http://localhost:8788/"))

            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 800})
            page.goto("http://localhost:8788/", wait_until="networkidle")
            check("title renders", "Renewal Guard" in page.title() or "Renewal Guard" in page.inner_text("h1"))
            check("protocol line present", "protocol" in page.inner_text("header").lower())

            # briefing
            page.click("#btn-brief")
            page.wait_for_function(
                "() => document.querySelector('#agent-log').textContent.includes('Approval is the human') || document.querySelectorAll('#agent-log .msg').length >= 8",
                timeout=20000,
            )
            time.sleep(0.6)
            agent_text = page.inner_text("#agent-log")
            check("briefing narrated gym", "FlexFitness" in agent_text)
            check("briefing narrated cloud", "NimbusDrive" in agent_text)
            check("briefing proposes to tray", "approval tray" in agent_text)
            check("protocol shown", "2025-11-25" in page.inner_text("header"))
            page.screenshot(path=str(SHOTS / "ui_1_briefing.png"), full_page=False)

            # tray populated
            page.wait_for_function(
                "() => (document.querySelector('#tray').textContent.match(/recommend:/g)||[]).length === 3",
                timeout=15000,
            )
            tray_text = page.inner_text("#tray").lower()
            check("tray shows 3 proposals", tray_text.count("recommend:") == 3, str(tray_text.count("recommend:")))
            check("tray shows impact", "£" in tray_text)
            page.screenshot(path=str(SHOTS / "ui_2_tray.png"), full_page=False)

            # guardrail
            page.click("#btn-rail")
            page.wait_for_selector(".denied", timeout=10000)
            check("guardrail denial visible", "DENIED" in page.inner_text(".denied"))
            check(
                "guardrail explains human approval",
                "human" in page.inner_text(".denied").lower(),
            )
            page.screenshot(path=str(SHOTS / "ui_3_guardrail.png"), full_page=False)

            # approve the gym cancellation
            cards = page.locator(".tray-card", has_text="FlexFitness Monthly")
            check("gym card present", cards.count() == 1, str(cards.count()))
            cards.first.locator("button", has_text="Approve").click()
            page.wait_for_function(
                "() => document.querySelector('#statusbar').textContent.includes('decision recorded')",
                timeout=15000,
            )
            time.sleep(0.5)
            state = page.evaluate("fetch('/api/state').then(r=>r.json())")
            gym = next(r for r in state["renewals"] if r["id"] == "gym-flex")
            check("gym cancelled via UI", gym["status"] == "cancelled_by_human", gym["status"])
            decided_cards = page.locator(".tray-card.decided")
            check("decided card greyed", decided_cards.count() >= 1)
            page.screenshot(path=str(SHOTS / "ui_4_approved.png"), full_page=False)

            # reject the cloud downgrade
            cards = page.locator(".tray-card", has_text="NimbusDrive 2TB")
            cards.first.locator("button", has_text="Reject").click()
            page.wait_for_function(
                "() => document.querySelector('#statusbar').textContent.includes('decision recorded')",
                timeout=15000,
            )
            time.sleep(0.5)
            state = page.evaluate("fetch('/api/state').then(r=>r.json())")
            cloud = next(r for r in state["renewals"] if r["id"] == "cloud-2tb")
            check("rejected downgrade keeps cloud active", cloud["status"] == "active", cloud["status"])
            rej = [p for p in state["proposals"] if p["status"] == "rejected"]
            check("proposal recorded rejected", len(rej) >= 1)
            page.screenshot(path=str(SHOTS / "ui_5_rejected.png"), full_page=False)

            browser.close()
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
