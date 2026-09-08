# ReleaseKeeper — demo video narration (target 4:15, hard cap 5:00)

Each `## S<n>` block is one narrated segment. `visual:` names the clip the recorder produces
(`demo/record.py`). Timing column = planned seconds for the assembled cut; the clip is cut or
sped up to fit the narration, never the other way round. Every number spoken here traces to
`docs/e2e_saju_1.1.17.md`.

## S1 · title (visual: slide_title) · 0:00–0:20
ReleaseKeeper is a release-ops agent for solo app developers, built on the Strands Agents SDK
for the Agents for Humans hackathon. I ship a small fortune-reading app alone, and every release
has the same four chores that are easy to get slightly wrong. This agent does them, and checks its
own work.

## S2 · problem (visual: slide_problem) · 0:20–0:45
The four chores. One: does the store copy say what the rules require — that the results are
AI-generated, that it is not advice, how refunds work? Two: when a refund webhook arrives at
2 a.m., which of the twenty-one RevenueCat event types is it, and what do I revoke? Three: turning
forty commits into release notes a user can read, under the five-hundred-character store cap.
Four: a store listing in a language I do not write well. Each one is a tool.

## S3 · tool ① disclosure check (visual: tab_check_bad + tab_check_live) · 0:45–1:30
Tool one is a deterministic rule engine — no model call. Here is an invented listing for a fake
app: "one hundred percent accurate readings", no AI disclosure, no refund line. Run tool: verdict
BLOCK, seven findings, each with the fix. Now the real thing — the live Google Play listing of
my app, pulled from the Play Developer API. Ship with fixes: one yellow, the missing
minimum-age line. Ask the agent, and the model explains the same findings in plain words. The
findings come from Python; the model only narrates them.

## S4 · tool ② refund triage (visual: tab_refund) · 1:30–2:05
Tool two: a RevenueCat webhook body. This one is a CANCELLATION with cancel reason
CUSTOMER_SUPPORT — that is the shape a refund actually arrives in. There is no REFUND event type; a
naive handler waiting for one never fires. The agent classifies it as a refund, tells me to revoke the entitlement, drafts
the reply to the customer, and warns that Play credentials must be registered before the
webhook can be trusted. About four seconds.

## S5 · tool ③ release notes (visual: tab_notes) · 2:05–2:50
Tool three: the real git log of my last release — thirty-nine commits, written in Korean as
session logs, so I tick "keep internal commits". The tool's keyword pass sorts them; the agent
writes the user-facing entries in plain English plus a store block. The interesting part is the post-check. In an earlier run the model produced a
six-hundred-twenty-two character store block and labelled it "under five hundred". A cap is not
a judgment call, so the page re-runs the length check itself. The answer cannot report its own
grade.

## S6 · tool ④ store listing (visual: tab_listing) · 2:50–3:35
Tool four: my app has no Korean listing. The agent calls listing_brief — the rules, caps and
required lines for that language and store — writes the listing, then calls listing_check on
what it wrote. The page runs the check once more, independently. Every field under the cap,
required lines present, no hype vocabulary. The generative step is bracketed: a brief before,
a check after.

## S7 · architecture (visual: slide_arch) · 3:35–4:00
One Strands Agent, four Python tools, a LiteLLM model provider pointed at an OpenRouter free
model — so the whole thing costs nothing to run. One FastAPI process serves this page, a CLI,
and the Amazon Bedrock AgentCore Runtime contract: slash-ping and slash-invocations. That
contract is implemented and verified locally, but not deployed — AgentCore is optional, and I
chose to skip it.

## S8 · limits + close (visual: slide_close) · 4:00–4:20
What it cannot catch: the Korean listing transliterated "BaZi" into the Korean word for
trousers. A human still reads the last draft. Code is public under MIT, tested on a real
release, reused rule logic disclosed in the README. Thanks for watching.
