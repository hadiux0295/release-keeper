"""Amazon Bedrock AgentCore Runtime surface — the HTTP service contract on top of ``web.app``.

AgentCore Runtime hosts any ARM64 container that listens on ``0.0.0.0:8080`` and exposes
``POST /invocations`` (JSON in, JSON out) and ``GET /ping`` (``{"status": "Healthy"}``).
Source: AWS docs "HTTP protocol contract" and "Get started without the AgentCore CLI"
(docs.aws.amazon.com/bedrock-agentcore/latest/devguide/, fetched 2026-09-05).

This module adds those two routes to the existing FastAPI app, so one container serves the
demo page, ``/api/*`` and the AgentCore contract. Nothing here needs AWS credentials to run
locally; the model endpoint is still whatever ``RK_MODEL`` / ``RK_API_BASE`` / ``RK_API_KEY``
point at (set them as runtime environment variables when deploying).

    uvicorn release_keeper.agentcore:app --host 0.0.0.0 --port 8080
    curl localhost:8080/ping
    curl -X POST localhost:8080/invocations -H 'content-type: application/json' \
         -d '{"tool": "check", "text": "…store listing…", "payments": true, "category": "fortune"}'

Payload shapes accepted by ``/invocations``:

* ``{"prompt": "<free text>"}`` — the plain AgentCore convention; goes to the Strands agent
  (needs an API key, otherwise 503 like the web page).
* ``{"tool": "check|refund|notes|listing", "mode": "raw|agent", ...fields}`` — the same
  fields as ``/api/<tool>`` on the web page. ``raw`` runs the deterministic tool with no LLM.

Response: ``{"status": "success", "tool": ..., "mode": ..., "response": <tool or agent output>}``.
Errors keep their HTTP status (400 bad input, 502 model failure, 503 no key); AgentCore
surfaces those to the caller as ``RuntimeClientError`` (HTTP 424) with the body preserved.
"""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, Request

from .web import app, CheckIn, RefundIn, NotesIn, ListingIn, api_check, api_refund, api_notes, api_listing, _agent

_TOOLS = {
    "check": (CheckIn, api_check),
    "refund": (RefundIn, api_refund),
    "notes": (NotesIn, api_notes),
    "listing": (ListingIn, api_listing),
}


def invoke(payload: dict) -> dict:
    """Dispatch one AgentCore payload. Pure function so tests and the route share it."""
    if not isinstance(payload, dict):
        raise HTTPException(400, "payload must be a JSON object")
    tool = payload.get("tool")
    if tool is None:
        prompt = payload.get("prompt")
        if not isinstance(prompt, str) or not prompt.strip():
            raise HTTPException(400, "send {'prompt': '<text>'} or {'tool': 'check|refund|notes|listing', ...}")
        return {"status": "success", "tool": "agent", "mode": "agent", "response": _agent(prompt)}
    if tool not in _TOOLS:
        raise HTTPException(400, f"unknown tool {tool!r}; expected one of {sorted(_TOOLS)}")
    model_cls, handler = _TOOLS[tool]
    fields = {k: v for k, v in payload.items() if k != "tool"}
    try:
        body = model_cls(**fields)
    except Exception as e:                        # pydantic ValidationError → 400, not 500
        raise HTTPException(400, f"invalid fields for {tool}: {e}")
    out = handler(body)
    return {"status": "success", "tool": tool, "mode": body.mode, "response": out}


@app.post("/invocations")
async def invocations(request: Request) -> Any:
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(400, "body must be JSON")
    # Handlers are blocking (agent call) — run them in the threadpool like the /api routes.
    from starlette.concurrency import run_in_threadpool
    return await run_in_threadpool(invoke, payload)


@app.get("/ping")
def ping() -> dict:
    return {"status": "Healthy"}
