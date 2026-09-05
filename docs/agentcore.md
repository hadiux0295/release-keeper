# Amazon Bedrock AgentCore Runtime — status and deploy runbook

**Status (2026-09-05):** the AgentCore HTTP service contract is implemented
(`release_keeper/agentcore.py`) and verified locally. **Not deployed** — the author has no AWS
account yet. If it is deployed before submission this line changes; until then the Devpost entry
must not claim an AgentCore deployment.

## What is implemented

AgentCore Runtime hosts any ARM64 container that listens on `0.0.0.0:8080` and exposes
`POST /invocations` and `GET /ping` (AWS docs: *HTTP protocol contract*, *Get started without the
AgentCore CLI*, fetched 2026-09-05). `release_keeper/agentcore.py` adds exactly those two routes
to the existing FastAPI app, so one process serves the demo page, `/api/*` and the contract.

| Piece | File | State |
|---|---|---|
| `/ping` → `{"status": "Healthy"}` | `release_keeper/agentcore.py` | ✅ unit + live |
| `/invocations` — `{"prompt": …}` → Strands agent; `{"tool": "check|refund|notes|listing", "mode": "raw|agent", …}` → same handlers as `/api/*` | `release_keeper/agentcore.py` | ✅ unit + live |
| Error mapping: 400 bad payload · 502 model failure · 503 no API key (never a crash) | same | ✅ unit |
| ARM64 `Dockerfile` (python:3.12-slim, `pip install .[web]`, uvicorn on 8080) | `Dockerfile` | ✍️ written, **image not built** (no Docker daemon on the dev box that day, no aarch64 binfmt) |
| Tests | `tests/test_agentcore.py` (5) | ✅ 51/51 total |

Local verification on the dev PC (x86, OpenRouter free model, 2026-09-05 11:03 UTC):

```
uvicorn release_keeper.agentcore:app --host 0.0.0.0 --port 8080
curl localhost:8080/ping                       → {"status":"Healthy"}
POST /invocations {"tool":"check","mode":"raw",…}   → success · verdict BLOCK · 7 findings (0 LLM calls)
POST /invocations {"tool":"refund","mode":"agent",…} → success · 21.1 s · 1 tool call · reply draft returned
```

No AWS credentials are needed for any of the above: the model endpoint is whatever
`RK_MODEL` / `RK_API_BASE` / `RK_API_KEY` point at.

## Deploy runbook (account day, ~2 h budget)

Prerequisites: AWS account + credentials (`aws sts get-caller-identity` works), Node.js ≥ 20,
Docker daemon running (Container build only).

### Path A — AgentCore CLI (`npm install -g @aws/agentcore`)

The CLI scaffolds its own project (`agentcore create`) and, for Python code agents, expects the
agent under `app/<Name>/main.py`. Two options, in order of preference:

1. **Container build** (`--build Container`) with this repo's `Dockerfile`. Whether the CLI can
   point at an existing Dockerfile outside its scaffold was **not verified** from the docs — check
   `agentcore add agent --help` / the generated `agentcore/agentcore.json` first.
2. **CodeZip build** (default, no Docker): copy `release_keeper/` into the scaffold's `app/<Name>/`,
   make `main.py` do `from release_keeper.agentcore import app` and run uvicorn on 8080, add
   `strands-agents[litellm]`, `fastapi`, `uvicorn` to its `pyproject.toml`.

Then:

```
agentcore dev                      # local server + inspector (port 8080)
agentcore deploy --verbose         # CDK bootstrap on first run
agentcore status                   # runtime ARN
agentcore invoke --prompt-file examples/invoke_check.json
```

Model credentials: the OpenRouter key goes in `agentcore/.env.local` / `agentcore add credential`
(non-Bedrock provider), never in the repo. The runtime env must carry `RK_API_KEY`
(and optionally `RK_MODEL`, `RK_API_BASE`). Alternative with zero external keys: switch the model
to Bedrock via Strands' `BedrockModel` — not implemented here because it cannot be tested without
an account (pick the model id from the Bedrock console on the day, do not guess one).

### Path B — no CLI (verified from *Get started without the AgentCore CLI*)

```
docker buildx create --use
docker buildx build --platform linux/arm64 -t release-keeper:arm64 --load .
docker run --platform linux/arm64 -p 8080:8080 -e RK_API_KEY=… release-keeper:arm64   # smoke
aws ecr create-repository --repository-name release-keeper --region us-west-2
aws ecr get-login-password --region us-west-2 | docker login --username AWS --password-stdin <acct>.dkr.ecr.us-west-2.amazonaws.com
docker buildx build --platform linux/arm64 -t <acct>.dkr.ecr.us-west-2.amazonaws.com/release-keeper:latest --push .
python deploy_agent.py    # boto3 bedrock-agentcore-control create_agent_runtime(containerConfiguration.containerUri, roleArn, networkMode PUBLIC)
python invoke_agent.py    # boto3 bedrock-agentcore invoke_agent_runtime(agentRuntimeArn, runtimeSessionId ≥33 chars, payload)
```

Needs an IAM role for the runtime (docs: *IAM Permissions for AgentCore Runtime*) and a way to
pass `RK_API_KEY` to the container — check the `CreateAgentRuntime` request for an environment
variables field on the day (not verified here).

## Open items / things not verified

- Synchronous invocation timeout of the runtime: not found in the fetched docs (quota pages are
  JS-rendered). Longest measured chain is `listing` agent mode at 22–71 s. If the limit is tighter
  than that, serve `listing` over SSE (`text/event-stream`, allowed by the contract) or keep it
  raw-only on AgentCore.
- `/ping` `HealthyBusy` and `time_of_last_update` are not used (all work is synchronous).
- Session id header `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` is ignored — every invocation
  is single-turn by design (see README, Architecture).
