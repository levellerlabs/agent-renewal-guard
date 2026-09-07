# Agent Renewal Guard - Alexa+ MCP server for recurring-payment control

Self-hosted MCP server (spec 2025-11-25+, Streamable HTTP) that lets an Alexa+-style
voice agent watch recurring payments and PROPOSE renew/degrade/cancel recommendations
without ever being able to move money itself. Every consequential action is a proposal
that waits for human approval - the agent can advise, never execute.

Built for the Amazon Developer Hackathon 2026, Alexa+ track.

## Why

Recurring payments drain money quietly: the gym membership from February, the 2TB cloud
plan holding 180GB, the family streaming tier after the kids moved out. Voice is the
natural surface for this - "Alexa, what renewals need a decision this week?" - but you
do not want a voice agent holding your wallet. This server gives the agent eyes
(read-only spend facts) and a pen for suggestions, never keys to the till.

## What it is

- A single-file Python MCP server (`server.py`) built on the official `mcp` SDK
  (v1.29.1, Streamable HTTP transport, stateless mode).
- Tools are split into two privilege tiers so an Alexa+ orchestrator can be configured
  least-privilege:
  - `list_upcoming_renewals` (read-only): renewals inside a decision window with
    monthly-cost facts, utilisation signals and days-until-renewal.
  - `get_renewal_detail` (read-only): one renewal, full record including cost history.
  - `propose_decision` (proposals): record a recommend/dromote/cancel/keep suggestion
    with reasons. Writes to `state/proposals.json` ONLY. Nothing here touches a bank.
  - `list_proposals` (read-only): the pending human-approval tray, so a companion skill
    can speak them back and the human can approve/reject from the Alexa app.
  - `record_decision` (human-gated): marks a proposal approved/rejected. Intended to be
    called only by the approval surface (app/CLI), not exposed to the advising agent.
- Data lives in `state/renewals.json` (a documented, editable JSON file - bank CSV
  import scripts are possible but out of scope for the PoC).
- The server never holds bank credentials, never makes payments, never cancels
  anything. The strongest thing the agent can do is write a suggestion to a local file.

## Quick start

Requires Python 3.11+.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install "mcp[cli]>=1.29.1"
python server.py            # serves Streamable HTTP on http://localhost:8787/mcp
```

Smoke-test with the MCP inspector:

```bash
npx @modelcontextprotocol/inspector python server.py
```

Or a raw Streamable HTTP POST:

```bash
curl -s http://localhost:8787/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Then list tools:

```bash
curl -s http://localhost:8787/mcp -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -H 'Mcp-Session-Id: <from initialize response>' \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/list"}'
```

## State files

- `state/renewals.json` - the subscription ledger. Edit by hand or generate from a bank
  export. Schema documented at the top of the file.
- `state/proposals.json` - the proposal tray. Written by `propose_decision`, read by
  `list_proposals`, resolved by `record_decision`. Nothing else ever writes here.

## Agent Skills integration

The `skill/` directory contains an Alexa+ Agent Skill package
(`renewal-guard-skill/SKILL.md` + `resources/`) that teaches an Alexa+ orchestrator
when to call each tool and the approval-flow guardrails. Drop it into an agent runtime
that supports Agent Skills (open standard) and point it at this server's `/mcp` endpoint.

## Security model

- Two-tier tool split: advising tools cannot mutate proposals' approval state; the
  approval tool is documented for the human-side surface only.
- No bank credentials, no payment rails, no cancel APIs. The agent physically cannot
  spend money through this server.
- Proposals carry an audit trail: who proposed, when, reasons, and the exact facts the
  proposal was based on (replayable from the ledger).

## License

MIT
