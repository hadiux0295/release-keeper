"""One-page web surface (FastAPI) — the same four tools as the CLI, behind a form.

    pip install -e ".[web]"
    uvicorn release_keeper.web:app --host 0.0.0.0 --port 8090

Every endpoint takes ``mode``: ``raw`` runs the deterministic tool only (no LLM, instant),
``agent`` sends the request through the Strands agent. Endpoints are plain ``def`` so the
blocking agent call runs in FastAPI's threadpool. A missing API key is a 503 in agent mode,
never a crash: ``build_model`` raises SystemExit, which must not take the worker down.

The listing endpoint re-runs ``listing_check`` on whatever the model returned, exactly like
the CLI, so the web surface keeps the "deterministic check has the last word" property.
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from .tools import run_disclosure_check, run_refund_triage, run_release_notes, run_listing_brief, run_listing_check

app = FastAPI(title="ReleaseKeeper", version="0.1.0", docs_url="/docs")

from .agent import DEFAULT_MODEL

_MODEL = os.environ.get("RK_MODEL", DEFAULT_MODEL)


def _has_key() -> bool:
    return bool(os.environ.get("RK_API_KEY") or os.environ.get("OPENROUTER_API_KEY") or os.environ.get("OPENAI_API_KEY"))


def _agent(prompt: str) -> dict:
    if not _has_key():
        raise HTTPException(503, "Agent mode needs RK_API_KEY (or OPENROUTER_API_KEY). Raw mode works without it.")
    from .agent import run_agent
    try:
        text, meta = run_agent(prompt)
    except SystemExit as e:                       # build_model() guard, defensive
        raise HTTPException(503, str(e))
    except Exception as e:                        # model/endpoint failure → readable error, worker survives
        raise HTTPException(502, f"agent error: {type(e).__name__}: {e}")
    return {"answer": text, "meta": meta}


# ----------------------------------------------------------------------------- request bodies

class CheckIn(BaseModel):
    text: str
    payments: bool = False
    accounts: bool = False
    emotional: bool = False
    category: str = "general"
    mode: str = "raw"


class RefundIn(BaseModel):
    event: str                                  # RevenueCat webhook body (JSON text)
    products: str = "{}"                        # JSON text product_id -> {kind,label,grant}
    app_name: str = "our app"
    lang: str = "en"
    mode: str = "raw"


class NotesIn(BaseModel):
    git_log: str = ""
    repo: Optional[str] = None                  # server-side path; only used when git_log is empty
    since: Optional[str] = None
    version: Optional[str] = None
    lang: str = "en"
    store: str = "play"
    include_internal: bool = False
    mode: str = "raw"


class ListingIn(BaseModel):
    app_facts: str                              # JSON text (see examples/app_facts_example.json)
    lang: str = "en"
    store: str = "play"
    listing: Optional[str] = None               # JSON text of a drafted listing → deterministic check only
    mode: str = "raw"                           # raw = print the brief; agent = brief → write → check


# ----------------------------------------------------------------------------- endpoints

@app.get("/health")
def health() -> dict:
    return {"ok": True, "model": _MODEL, "agent_available": _has_key()}


@app.post("/api/check")
def api_check(b: CheckIn) -> Any:
    if b.mode == "raw":
        return json.loads(run_disclosure_check(b.text, has_payments=b.payments, has_accounts=b.accounts,
                                               has_free_text_emotional_input=b.emotional, category=b.category).to_json())
    flags = (f"app_uses_ai=true, has_payments={b.payments}, has_accounts={b.accounts}, "
             f"has_free_text_emotional_input={b.emotional}, category={b.category}")
    return _agent(f"Run disclosure_check on the copy below with {flags}, then summarize.\n\n---\n{b.text}")


@app.post("/api/refund")
def api_refund(b: RefundIn) -> Any:
    try:
        products = json.loads(b.products or "{}")
    except json.JSONDecodeError as e:
        raise HTTPException(400, f"products is not JSON: {e}")
    if b.mode == "raw":
        return json.loads(run_refund_triage(b.event, products=products, app_name=b.app_name, language=b.lang).to_json())
    return _agent(f"Run refund_triage on the RevenueCat webhook event below (app_name={b.app_name!r}, language={b.lang!r}, "
                  f"products_json={json.dumps(products)!r}), then tell me what to do.\n\n---\n{b.event}")


@app.post("/api/notes")
def api_notes(b: NotesIn) -> Any:
    log = b.git_log
    if not log.strip():
        if not b.repo:
            raise HTTPException(400, "Paste a git log or give a server-side repo path.")
        rng = f"{b.since}..HEAD" if b.since else "HEAD"
        try:
            log = subprocess.run(["git", "-C", b.repo, "log", "--oneline", "--no-merges", rng],
                                 check=True, capture_output=True, text=True, timeout=20).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError) as e:
            raise HTTPException(400, f"git log failed: {e}")
    if b.mode == "raw":
        return json.loads(run_release_notes(log, version=b.version, language=b.lang, store=b.store,
                                            include_internal=b.include_internal).to_json())
    out = _agent(f"Run release_notes on the git log below (version={b.version!r}, language={b.lang!r}, store={b.store!r}, "
                 f"include_internal={b.include_internal}), then present the notes.\n\n---\n{log}")
    from .agent import notes_post_check
    from .tools.release_notes import PLAY_WHATS_NEW_MAX, APP_STORE_WHATS_NEW_MAX
    pc = notes_post_check(out["answer"], PLAY_WHATS_NEW_MAX if b.store == "play" else APP_STORE_WHATS_NEW_MAX)
    out["post_check"] = pc
    out["verdict"] = "OK" if pc["ok"] else ("UNVERIFIED" if pc["block"] is None else "BLOCK")
    return out


@app.post("/api/listing")
def api_listing(b: ListingIn) -> Any:
    if b.listing:                                                   # validate a draft, no LLM
        return json.loads(run_listing_check(b.listing, b.app_facts, language=b.lang, store=b.store).to_json())
    if b.mode == "raw":
        return json.loads(run_listing_brief(b.app_facts, language=b.lang, store=b.store).to_json())
    out = _agent(f"Write the {b.store} store listing in language {b.lang!r} for the app described below. "
                 f"Call listing_brief first (language={b.lang!r}, store={b.store!r}), write the listing, "
                 f"then call listing_check on it.\n\n---\n{b.app_facts}")
    from .agent import listing_post_check
    listing, rep = listing_post_check(out["answer"], b.app_facts, language=b.lang, store=b.store)
    out["listing"] = listing
    out["post_check"] = json.loads(rep.to_json()) if rep is not None else None
    out["verdict"] = rep.verdict if rep is not None else "UNVERIFIED"
    return out


# ----------------------------------------------------------------------------- page

@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE.replace("{{MODEL}}", _MODEL).replace("{{AGENT}}", "on" if _has_key() else "off (raw mode only)")


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>ReleaseKeeper</title>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c1f24;--mute:#6b7280;--line:#e4e7ec;--acc:#0f766e;--red:#b91c1c;--yel:#a16207;--grn:#15803d}
*{box-sizing:border-box}body{margin:0;font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;background:var(--bg);color:var(--ink)}
header{padding:20px 24px 8px}h1{margin:0;font-size:22px}h1 small{font-weight:400;color:var(--mute);font-size:14px;margin-left:8px}
nav{display:flex;gap:6px;padding:0 24px;border-bottom:1px solid var(--line);background:var(--card)}
nav button{border:0;background:none;padding:12px 14px;font:inherit;cursor:pointer;color:var(--mute);border-bottom:2px solid transparent}
nav button.on{color:var(--acc);border-color:var(--acc);font-weight:600}
main{display:grid;grid-template-columns:minmax(320px,1fr) minmax(320px,1fr);gap:16px;padding:16px 24px}
@media(max-width:860px){main{grid-template-columns:1fr}}
section{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px}
label{display:block;font-size:13px;color:var(--mute);margin:10px 0 4px}textarea,input[type=text],select{width:100%;font:13px/1.4 ui-monospace,Menlo,monospace;padding:8px;border:1px solid var(--line);border-radius:6px;background:#fafafa}
textarea{min-height:170px;resize:vertical}.row{display:flex;gap:14px;flex-wrap:wrap;align-items:center;margin-top:10px}.row label{margin:0;display:inline-flex;gap:6px;align-items:center;color:var(--ink)}
.run{margin-top:14px;display:flex;gap:10px;align-items:center}.run button{background:var(--acc);color:#fff;border:0;border-radius:6px;padding:9px 16px;font:inherit;font-weight:600;cursor:pointer}
.run button.alt{background:#fff;color:var(--acc);border:1px solid var(--acc)}.run span{color:var(--mute);font-size:13px}
pre{white-space:pre-wrap;word-break:break-word;font:12.5px/1.45 ui-monospace,Menlo,monospace;background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;min-height:200px;max-height:70vh;overflow:auto;margin:0}
.badge{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;margin-right:6px}
.BLOCK{background:#fee2e2;color:var(--red)}.SHIP_WITH_FIXES{background:#fef3c7;color:var(--yel)}.OK{background:#dcfce7;color:var(--grn)}.UNVERIFIED{background:#e5e7eb;color:var(--mute)}
footer{padding:12px 24px 28px;color:var(--mute);font-size:12.5px;border-top:1px solid var(--line)}
.hide{display:none}.md{font:14px/1.55 system-ui,sans-serif;background:#fff;color:var(--ink);border:1px solid var(--line)}
</style></head><body>
<header><h1>ReleaseKeeper <small>release-ops agent for solo app developers · Strands Agents SDK</small></h1></header>
<nav>
<button class="on" data-t="check">① disclosure check</button><button data-t="refund">② refund triage</button>
<button data-t="notes">③ release notes</button><button data-t="listing">④ store listing</button>
</nav>
<main>
<section id="form">
 <form id="f-check" class="tab">
  <label>Copy to scan (listing / result screen / ToS)</label><textarea name="text" placeholder="Paste app copy…"></textarea>
  <div class="row"><label><input type="checkbox" name="payments"> sells something</label><label><input type="checkbox" name="accounts"> has accounts</label>
  <label><input type="checkbox" name="emotional"> free-text emotional input</label>
  <label>category <select name="category"><option>general</option><option>fortune</option><option>interpretation</option><option>wellness</option><option>game</option><option>productivity</option></select></label></div>
 </form>
 <form id="f-refund" class="tab hide">
  <label>RevenueCat webhook body (JSON)</label><textarea name="event" placeholder='{"event": {"type": "CANCELLATION", "cancel_reason": "CUSTOMER_SUPPORT", ...}}'></textarea>
  <label>products map (JSON, optional) — product_id → {kind, label, grant}</label><textarea name="products" style="min-height:70px">{}</textarea>
  <div class="row"><label>app name <input type="text" name="app_name" value="our app" style="width:160px"></label>
  <label>reply language <select name="lang"><option>en</option><option>ko</option></select></label></div>
 </form>
 <form id="f-notes" class="tab hide">
  <label>git log --oneline (previous release..HEAD)</label><textarea name="git_log" placeholder="a1b2c3d feat(saju): add Japanese listing&#10;c7bf446 fix: crash on empty birth time…"></textarea>
  <div class="row"><label>version <input type="text" name="version" placeholder="1.2.0" style="width:90px"></label>
  <label>lang <select name="lang"><option>en</option><option>ko</option></select></label>
  <label>store <select name="store"><option>play</option><option>appstore</option></select></label>
  <label><input type="checkbox" name="include_internal"> keep internal commits</label></div>
 </form>
 <form id="f-listing" class="tab hide">
  <label>App facts (JSON — name, tagline, category, model_name, features[], has_payments, has_accounts, …)</label><textarea name="app_facts" placeholder='{"name": "Saju Club", "category": "fortune", "model_name": "Google Gemini", ...}'></textarea>
  <label>Drafted listing to validate (JSON, optional — deterministic check only)</label><textarea name="listing" style="min-height:70px"></textarea>
  <div class="row"><label>lang <input type="text" name="lang" value="en" style="width:60px"></label>
  <label>store <select name="store"><option>play</option><option>appstore</option></select></label></div>
 </form>
 <div class="run"><button id="raw" type="button">Run tool (no LLM)</button><button id="agent" type="button" class="alt">Ask the agent</button><span id="status"></span></div>
</section>
<section><div id="verdict" style="margin-bottom:8px"></div><pre id="out">Result appears here.</pre></section>
</main>
<footer>Agent answers are generated by an AI language model (<code>{{MODEL}}</code>, agent mode {{AGENT}}). Findings are risk flags with a concrete fix, not legal advice. “Run tool” executes deterministic Python only.</footer>
<script>
const tabs=[...document.querySelectorAll('nav button')],forms={};document.querySelectorAll('form.tab').forEach(f=>forms[f.id.slice(2)]=f);let cur='check';
tabs.forEach(b=>b.onclick=()=>{tabs.forEach(x=>x.classList.toggle('on',x===b));cur=b.dataset.t;Object.values(forms).forEach(f=>f.classList.toggle('hide',f.id!=='f-'+cur));});
function body(){const f=forms[cur],d={};for(const el of f.elements){if(!el.name)continue;d[el.name]=el.type==='checkbox'?el.checked:el.value;}return d;}
async function run(mode){const st=document.getElementById('status'),out=document.getElementById('out'),v=document.getElementById('verdict');
 st.textContent=mode==='agent'?'agent running… (10–60 s on the free model)':'running…';out.textContent='';v.innerHTML='';const t0=Date.now();
 try{const r=await fetch('/api/'+cur,{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({...body(),mode})});
  const j=await r.json();if(!r.ok){out.textContent='HTTP '+r.status+' — '+(j.detail||JSON.stringify(j));st.textContent='';return;}
  const verdict=j.verdict||(j.summary&&j.summary.verdict)||(j.post_check&&j.post_check.verdict);
  if(verdict)v.innerHTML='<span class="badge '+verdict+'">'+verdict+'</span>'+(j.event_class?'<span class="badge OK">'+j.event_class+'</span>':'');
  if(j.answer){out.className='md';out.textContent=j.answer+(j.post_check?'\\n\\n— independent post-check —\\n'+JSON.stringify(j.post_check,null,2):'');
   st.textContent=(j.meta?j.meta.seconds+' s · '+j.meta.input_tokens+'/'+j.meta.output_tokens+' tokens · '+j.meta.tool_calls+' tool call(s)':'');}
  else{out.className='';out.textContent=JSON.stringify(j,null,2);st.textContent=((Date.now()-t0)/1000).toFixed(2)+' s';}
 }catch(e){out.textContent=String(e);st.textContent='';}}
document.getElementById('raw').onclick=()=>run('raw');document.getElementById('agent').onclick=()=>run('agent');
</script></body></html>"""
