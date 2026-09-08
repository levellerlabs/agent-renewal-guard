# Agent Renewal Guard - Amazon Developer Hackathon submission text

Track: Alexa+ | Draft fields for devpost.com/submit-to/30992.../manage/submissions
Video: agent-renewal-guard-demo.mp4 (upload PUBLIC to YouTube, one Joe tap)

## Inspiration (overview field)

Voice agents are keen to help with household money, but the obvious design -
give the agent the ability to cancel and pay - is the one nobody should ship.
Agent Renewal Guard is a self-hosted MCP server that lets a voice agent fully
participate in recurring-payment decisions while being structurally unable to
move money: the privilege split is the product.

## What it does

A voice briefing agent can see what renewals are coming up (monthly-equivalent
cost, days until renewal, utilisation signals), pull detail on any one of them,
and record a spoken-friendly recommendation into a human approval tray. The
human approves or rejects from the companion tray surface. On approval of a
cancel or switch, the renewal leaves the decision window in the ledger. No
external action, no payment, no cancellation - by design.

## How we built it

- MCP server on the official Python SDK (FastMCP, mcp 1.29.1), Streamable HTTP
  transport, stateless mode. initialize negotiates protocolVersion 2025-11-25
  - the hackathon-required minimum - verified live against the running server.
- Five tools in two privilege tiers: read tools (list_upcoming_renewals,
  get_renewal_detail, list_proposals), one proposal write
  (propose_decision - writes only state/proposals.json), and the human-side
  record_decision, documented as not-for-agent.
- The separation lives in the CLIENT configuration: the voice-briefing client
  is a real least-privilege MCP client that grants only the four advising
  tools. record_decision is held by the approval-tray client alone. An agent
  attempting to record its own decision is refused client-side before any
  wire call - we demo exactly this refusal.
- Simulated Alexa+ web experience: a two-pane console where the left pane is
  that real least-privilege client and the right pane is the only
  record_decision holder. Paper field-notebook UI, no dark-AI aesthetic.
- Agent Skill package (skill/renewal-guard-skill/SKILL.md) for the Alexa+
  orchestrator layer: when-to-use, tool use, hard rules, voice style.
- Tests: 17/17 server e2e, 23/23 console HTTP, 16/16 browser UI (real
  Chromium clicks through the full propose -> guardrail -> approve loop).

## Challenges we ran into

Making least privilege REAL rather than prompt-deep. The server exposes five
tools; the discipline is in what each client grants. We had to make the demo
prove the refusal honestly - the guardrail segment shows an actual attempted
record_decision being refused because the tool was never granted to that
client, not a simulated error.

## Accomplishments we're proud of

The guardrail demo is the whole product in eight lines of transcript: agent
refused, tray approves, human decided. And everything shown is real captured
traffic - the initialize handshake, the tools/list, the scan, the proposal,
the approval - replayed from a live run.

## What we learned

That client-side tool grants are a stronger safety boundary than any prompt
rule: an agent cannot be prompt-injected into using a tool it does not hold.

## What's next for Agent Renewal Guard

Optional Strands SDK wrapper for the advising loop (AWS Builder
mini-challenge), and a real card-statement importer so the renewal ledger
fills itself.

## Built with

Python, mcp SDK (FastMCP, Streamable HTTP), Starlette/Uvicorn, Playwright
(tests + capture), PIL/ffmpeg (video), edge-tts.

## Repo

https://github.com/levellerlabs/agent-renewal-guard (MIT)

## Tags

alexa, mcp, agent-skills, voice-agent, streamable-http, human-in-the-loop
