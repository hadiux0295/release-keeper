"""ReleaseKeeper agent — Strands Agent wired to an OpenAI-compatible endpoint via LiteLLM.

Model and endpoint come from environment only so the same code runs against
OpenRouter (free tier), Amazon Bedrock, or any OpenAI-compatible server.

  RK_MODEL     model id, LiteLLM style (default: openai/nvidia/nemotron-3-super-120b-a12b:free)
  RK_API_BASE  OpenAI-compatible base URL (default: https://openrouter.ai/api/v1)
  RK_API_KEY   API key (falls back to OPENROUTER_API_KEY, then OPENAI_API_KEY)
"""
from __future__ import annotations

import json
import os
import re
import time

from strands import Agent
from strands.models.litellm import LiteLLMModel

from .tools import disclosure_check, refund_triage, release_notes, listing_brief, listing_check

DEFAULT_MODEL = "openai/nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_API_BASE = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are ReleaseKeeper, a release-operations assistant for solo app developers.
You have tools; pick the one that matches the request, call it once, then answer from its output.
Exception: a store-listing request is a chain — call listing_brief, write the listing, call listing_check on it, then answer.
General rules:
- Never issue legal verdicts. Findings are risks, phrased as "this creates X risk because Y".
- Repeat any needs_input list from the tool verbatim under "Needs your input".
- Repeat any warnings from the tool verbatim under "Warnings".
- Be concise: a developer should be able to act on the answer in under a minute.
Per tool:
- disclosure_check: report red findings first, each with its concrete fix, then yellow. Skip items that passed.
- refund_triage: state the event_class in one line, then the entitlement_action as an imperative, then whether a reply is needed. If reply_draft is present, output it verbatim inside a fenced block; do not invent facts not in the event.
- release_notes: the tool returns DRAFTS built from raw commit subjects — never copy them. Step 1, filter: keep an entry only if a person using the app would notice it (a screen, wording, price, a bug they could hit). Reports, measurements, specs, prompt experiments, design samples, deploy/ops logs, tests, docs, planning = internal — list those in one line under "Dropped as internal" (count + a few words). Step 2, rewrite each kept entry in plain user language: one line, what changed for the user, no file names, ticket ids, section numbers, version tags or team jargon; translate to the requested language. Keep the grouping Important / New / Improved / Fixed. Step 3, write the store what's-new yourself from the rewritten entries as ONE fenced ```text block, bullet per line, strictly under store_cap characters. Step 4, list unclassified commits as questions ("user-facing?") quoting their subject, not their hash.
- listing_brief / listing_check: write the listing strictly inside the caps and end the full description with the disclosure block verbatim. Output the final listing as ONE fenced ```json block with exactly the fields in output_format.json_fields, then the listing_check verdict and any red/yellow findings. If listing_check returns red, fix and re-check once.
"""

ALL_TOOLS = [disclosure_check, refund_triage, release_notes, listing_brief, listing_check]


def build_model() -> LiteLLMModel:
    api_key = os.environ.get("RK_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set RK_API_KEY (or OPENROUTER_API_KEY / OPENAI_API_KEY)")
    return LiteLLMModel(
        client_args={"api_key": api_key, "api_base": os.environ.get("RK_API_BASE", DEFAULT_API_BASE)},
        model_id=os.environ.get("RK_MODEL", DEFAULT_MODEL),
        params={
            "max_tokens": 6000,
            "temperature": 0.2,
            # Free reasoning models can leak long hidden reasoning into the reply and hit max_tokens;
            # OpenRouter honours this flag, other endpoints ignore unknown extra_body keys.
            "extra_body": {"reasoning": {"enabled": False}},
        },
    )


def build_agent(tools=None, callback_handler=None) -> Agent:
    return Agent(
        model=build_model(),
        tools=tools or ALL_TOOLS,
        system_prompt=SYSTEM_PROMPT,
        callback_handler=callback_handler,
    )


def run_agent(prompt: str, agent: Agent | None = None) -> tuple[str, dict]:
    """One request → (answer text, metrics). Shared by the CLI and the web page."""
    agent = agent or build_agent()
    t0 = time.time()
    result = agent(prompt)
    usage = getattr(result.metrics, "accumulated_usage", {}) or {}
    tool_calls = sum(int(getattr(m, "call_count", 0)) for m in (getattr(result.metrics, "tool_metrics", None) or {}).values())
    meta = {
        "seconds": round(time.time() - t0, 1),
        "input_tokens": usage.get("inputTokens", 0),
        "output_tokens": usage.get("outputTokens", 0),
        "tool_calls": tool_calls,
        "model": os.environ.get("RK_MODEL", DEFAULT_MODEL),
    }
    return str(result), meta


def extract_json_block(text: str):
    """Last fenced ```json block in an agent answer, or None."""
    blocks = re.findall(r"```json\s*(\{.*?\})\s*```", text, re.S)
    for b in reversed(blocks):
        try:
            return json.loads(b)
        except json.JSONDecodeError:
            continue
    return None


def listing_post_check(answer: str, app_facts_json: str, *, language: str, store: str):
    """Independent verification of a listing the model wrote: the deterministic check has
    the last word, whatever the model claimed. Returns (listing_dict | None, report | None)."""
    from .tools import run_listing_check
    listing = extract_json_block(answer)
    if listing is None:
        return None, None
    return listing, run_listing_check(listing, app_facts_json, language=language, store=store)


def notes_post_check(answer: str, cap: int) -> dict:
    """Deterministic check of the store what's-new the model wrote: the LAST fenced block in the
    answer is measured against the store cap. Returns {"chars", "cap", "ok", "block"}; block None if absent."""
    blocks = re.findall(r"```[a-zA-Z]*\s*\n(.*?)```", answer, re.S)
    if not blocks:
        return {"chars": 0, "cap": cap, "ok": False, "block": None}
    block = blocks[-1].strip()
    return {"chars": len(block), "cap": cap, "ok": len(block) <= cap, "block": block}
