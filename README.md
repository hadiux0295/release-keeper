# ReleaseKeeper

Release-operations agent for solo app developers. Built for the
[Agents for Humans](https://agentsforhumans.devpost.com) hackathon (2026) on the
**Strands Agents SDK**.

Every app release repeats the same judgment-heavy chores: check that the store
listing and result screens carry the disclosures an AI app needs, classify
refund requests and draft replies, write release notes, localize the listing.
Miss one and you get a store rejection or a chargeback. ReleaseKeeper is one
Strands agent with four tools that does these from real inputs (copy text,
payment-webhook payloads, git log).

## Status (building 2026-09-04 → 09-13)

| Tool | State |
|---|---|
| `disclosure_check` — scan copy for AI disclosure, framing, refund/deletion/age/crisis lines | ✅ |
| `refund_triage` — classify a RevenueCat-style event and draft a reply | planned |
| `release_notes` — turn `git log` into user-facing notes | planned |
| `listing_writer` — localized store listing from a feature list | planned |

## Quick start

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
pytest -q                                   # tool unit tests, no LLM needed

# deterministic scan, no LLM
python -m release_keeper check examples/saju_listing_bad.txt --payments --category fortune --raw

# agent run (OpenAI-compatible endpoint; OpenRouter free tier by default)
export OPENROUTER_API_KEY=...
python -m release_keeper check examples/saju_listing_bad.txt --payments --category fortune
```

Environment: `RK_MODEL` (LiteLLM id, default `openai/nvidia/nemotron-3-super-120b-a12b:free`),
`RK_API_BASE` (default OpenRouter), `RK_API_KEY`.

## Design

- Single Strands `Agent`, tools are deterministic Python where possible so results are
  reproducible; the model plans, calls, and explains.
- Each request is a single turn (prompt → tool → answer). Chat-completions endpoints do not
  carry reasoning content across turns, so the agent is built as a tool chain, not a chat.
- Findings are **risk flags with a concrete fix**, never legal verdicts. Anything not
  decidable from the inputs is returned under `needs_input`.

## Reused material (disclosure per hackathon rules)

All code in this repository was written during the submission period. The following
pre-existing material by the same author informed it:

- The disclosure checklist logic in `disclosure_check` is adapted from the author's internal
  pre-launch review checklist used for their own apps (rules re-implemented as generic code;
  no text copied).
- Example listing copy in `examples/` is adapted from the author's own published app
  ([Saju Club](https://saju.hun-is.com)).
- The refund-event classification rule (planned tool) comes from the author's own
  RevenueCat webhook handling experience.

## License

MIT — see `LICENSE`.
