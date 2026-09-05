"""ReleaseKeeper agent — Strands Agent wired to an OpenAI-compatible endpoint via LiteLLM.

Model and endpoint come from environment only so the same code runs against
OpenRouter (free tier), Amazon Bedrock, or any OpenAI-compatible server.

  RK_MODEL     model id, LiteLLM style (default: openai/nvidia/nemotron-3-super-120b-a12b:free)
  RK_API_BASE  OpenAI-compatible base URL (default: https://openrouter.ai/api/v1)
  RK_API_KEY   API key (falls back to OPENROUTER_API_KEY, then OPENAI_API_KEY)
"""
from __future__ import annotations

import os

from strands import Agent
from strands.models.litellm import LiteLLMModel

from .tools import disclosure_check, refund_triage, release_notes

DEFAULT_MODEL = "openai/nvidia/nemotron-3-super-120b-a12b:free"
DEFAULT_API_BASE = "https://openrouter.ai/api/v1"

SYSTEM_PROMPT = """You are ReleaseKeeper, a release-operations assistant for solo app developers.
You have tools; pick the one that matches the request, call it exactly once, then answer from its output.
General rules:
- Never issue legal verdicts. Findings are risks, phrased as "this creates X risk because Y".
- Repeat any needs_input list from the tool verbatim under "Needs your input".
- Repeat any warnings from the tool verbatim under "Warnings".
- Be concise: a developer should be able to act on the answer in under a minute.
Per tool:
- disclosure_check: report red findings first, each with its concrete fix, then yellow. Skip items that passed.
- refund_triage: state the event_class in one line, then the entitlement_action as an imperative, then whether a reply is needed. If reply_draft is present, output it verbatim inside a fenced block; do not invent facts not in the event.
- release_notes: output the markdown as-is, then the store_whats_new block with its character count, then list unclassified commits as questions ("user-facing?").
"""

ALL_TOOLS = [disclosure_check, refund_triage, release_notes]


def build_model() -> LiteLLMModel:
    api_key = os.environ.get("RK_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("Set RK_API_KEY (or OPENROUTER_API_KEY / OPENAI_API_KEY)")
    return LiteLLMModel(
        client_args={"api_key": api_key, "api_base": os.environ.get("RK_API_BASE", DEFAULT_API_BASE)},
        model_id=os.environ.get("RK_MODEL", DEFAULT_MODEL),
        params={
            "max_tokens": 3000,
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
