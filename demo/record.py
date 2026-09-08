"""Record the demo clips for the ReleaseKeeper video (see demo/narration.md).

Prereq: the web page is running, e.g.
    RK_API_KEY=... uvicorn release_keeper.web:app --port 8765
Run:
    python demo/record.py --out /path/to/clips [--base http://127.0.0.1:8765]

Outputs (all in --out):
    slide_*.png                 still frames (title / problem / architecture / close)
    tab_*.webm                  screen recordings of one tool tab each (silent)
    events.json                 per clip: seconds when the run was clicked and when the
                                result appeared, so the assembler can cut the wait

Needs `playwright` (python) with chromium installed. No key or environment value is ever
rendered: the page footer shows only the model id.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import shutil
import time

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parent.parent
EX = ROOT / "examples"
W, H = 1920, 1080
ZOOM = 1.5  # page CSS zoom so 15px UI text is legible at 1080p

SLIDE_CSS = """
<style>
html,body{margin:0;height:100%;background:#0f172a;color:#e2e8f0;font:28px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
.s{padding:90px 120px;height:100%;box-sizing:border-box;display:flex;flex-direction:column;justify-content:center}
h1{font-size:72px;margin:0 0 16px;color:#fff}h2{font-size:52px;margin:0 0 28px;color:#fff}
.sub{color:#94a3b8;font-size:30px}.acc{color:#2dd4bf}
ol,ul{margin:8px 0 0;padding-left:44px;font-size:34px}li{margin:14px 0}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:40px;margin-top:20px}
.box{border:2px solid #334155;border-radius:16px;padding:26px 30px;font-size:28px}
.box b{color:#2dd4bf;display:block;font-size:24px;letter-spacing:.06em;text-transform:uppercase;margin-bottom:8px}
code{font:26px ui-monospace,Menlo,monospace;color:#fbbf24}
.foot{position:absolute;left:120px;bottom:60px;color:#64748b;font-size:24px}
</style>
"""

SLIDES = {
    "slide_title": """
<div class="s"><h1>ReleaseKeeper</h1>
<div class="sub">release-ops agent for solo app developers</div>
<div class="sub" style="margin-top:40px">Strands Agents SDK · four tools that check their own work</div>
<div class="sub" style="margin-top:16px">Agents for Humans hackathon · 2026</div>
<div class="foot">github.com/hadiux0295/release-keeper · MIT</div></div>""",
    "slide_problem": """
<div class="s"><h2>Four release chores, four tools</h2>
<ol>
<li><span class="acc">Disclosure check</span> — does the copy say what the rules require?</li>
<li><span class="acc">Refund triage</span> — which RevenueCat event is this, what do I revoke?</li>
<li><span class="acc">Release notes</span> — 39 commits → plain English, under the 500-char cap</li>
<li><span class="acc">Store listing</span> — a language I don't write well, checked twice</li>
</ol>
<div class="foot">tested on a real release: Saju Today 1.1.17 · docs/e2e_saju_1.1.17.md</div></div>""",
    "slide_arch": """
<div class="s"><h2>Architecture</h2>
<div class="grid">
<div class="box"><b>Agent</b>one Strands <code>Agent</code> · four <code>@tool</code> functions · single-turn tool chain</div>
<div class="box"><b>Model</b><code>LiteLLMModel</code> → OpenRouter free model · zero running cost · swap with one env var</div>
<div class="box"><b>Surface</b>one FastAPI process: this page · CLI · <code>/ping</code> + <code>/invocations</code> (AgentCore Runtime contract)</div>
<div class="box"><b>Bracketed generation</b>brief before → model → check after · the page re-runs the check itself</div>
</div>
<div class="foot">AgentCore contract: implemented + verified locally · not deployed (optional, skipped by choice)</div></div>""",
    "slide_close": """
<div class="s"><h2>What it cannot catch</h2>
<ul>
<li>"BaZi" transliterated into the Korean word for trousers — vocabulary checks are blind to meaning</li>
<li>A human still reads the last draft</li>
</ul>
<h2 style="margin-top:60px">What it is</h2>
<ul>
<li>Public · MIT · pytest 51/51 · tested on a real release</li>
<li>Reused rule logic disclosed in the README</li>
</ul>
<div class="foot">github.com/hadiux0295/release-keeper · thanks for watching</div></div>""",
}


def read(name: str) -> str:
    return (EX / name).read_text(encoding="utf-8")


class Rec:
    def __init__(self, pw, base: str, out: pathlib.Path, name: str):
        self.base, self.out, self.name = base, out, name
        self.browser = pw.chromium.launch()
        self.ctx = self.browser.new_context(viewport={"width": W, "height": H},
                                            record_video_dir=str(out / "_raw"),
                                            record_video_size={"width": W, "height": H})
        self.page = self.ctx.new_page()
        self.t0 = time.time()
        self.events: list[dict] = []
        self.page.goto(base, wait_until="networkidle")
        self.page.evaluate(f"document.body.style.zoom='{ZOOM}'")
        self.hold(1.2)

    def mark(self, kind: str, **kw):
        self.events.append({"t": round(time.time() - self.t0, 2), "kind": kind, **kw})

    def hold(self, s: float):
        self.page.wait_for_timeout(int(s * 1000))

    def tab(self, key: str):
        self.page.click(f"nav button[data-t={key}]")
        self.hold(0.8)

    def fill(self, form: str, field: str, value: str):
        sel = f"#f-{form} [name={field}]"
        el = self.page.locator(sel)
        tag = el.evaluate("e=>e.tagName+':'+(e.type||'')")
        if tag.endswith("checkbox"):
            el.set_checked(bool(value))
        elif tag.startswith("SELECT"):
            el.select_option(value)
        else:
            el.fill(value)
        self.hold(0.5)

    def run(self, mode: str, hold_after: float = 5.0, retries: int = 4):
        """Click Run/Ask and wait for the result. A free-model upstream error (HTTP 502 shown in
        the output box) is retried after a pause; the retry clicks are logged so the assembler
        can cut them out too."""
        for attempt in range(retries + 1):
            self.page.evaluate("document.getElementById('out').textContent='';document.getElementById('status').textContent=''")
            self.mark("click", mode=mode, attempt=attempt)
            self.page.click(f"#{mode}")
            # result = status shows a number (seconds / meta line); error = output starts with "HTTP "
            self.page.wait_for_function(
                "()=>{const s=document.getElementById('status').textContent;const o=document.getElementById('out').textContent;"
                "return (o.length>0 && /\\d/.test(s)) || o.startsWith('HTTP ')}", timeout=240_000)
            out = self.page.text_content("#out") or ""
            if not out.startswith("HTTP "):
                break
            self.mark("error", text=out[:120])
            print(f"  {self.name}: {out[:80]} — retry {attempt + 1}/{retries}")
            self.hold(20)
        else:
            raise RuntimeError(f"{self.name}: model kept failing")
        self.mark("result", status=self.page.text_content("#status"),
                  verdict=self.page.text_content("#verdict"))
        self.hold(min(3.0, hold_after))
        # scroll the output box slowly so the tail (post-check block) is on screen at the end
        rest = max(0.0, hold_after - 3.0)
        steps = int(rest / 0.1)
        for i in range(steps):
            self.page.evaluate("(f)=>{const o=document.getElementById('out');o.scrollTop=f*(o.scrollHeight-o.clientHeight)}", (i + 1) / steps)
            self.hold(0.1)

    def close(self):
        self.mark("end")
        self.page.close()
        path = self.page.video.path()
        self.ctx.close()
        self.browser.close()
        dst = self.out / f"{self.name}.webm"
        shutil.move(path, dst)
        return {"clip": dst.name, "events": self.events}


def slides(pw, out: pathlib.Path):
    b = pw.chromium.launch()
    pg = b.new_page(viewport={"width": W, "height": H})
    for name, body in SLIDES.items():
        pg.set_content("<!doctype html><html><head><meta charset=utf-8>" + SLIDE_CSS + "</head><body>" + body + "</body></html>")
        pg.screenshot(path=str(out / f"{name}.png"))
    b.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--only", default="", help="comma list of clip names to record")
    a = ap.parse_args()
    out = pathlib.Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    only = set(filter(None, a.only.split(",")))
    want = lambda n: not only or n in only  # noqa: E731
    log: dict[str, dict] = {}
    if (out / "events.json").exists():
        log = json.loads((out / "events.json").read_text())

    with sync_playwright() as pw:
        if want("slides"):
            slides(pw, out)
            print("slides done")

        if want("tab_check"):
            r = Rec(pw, a.base, out, "tab_check")
            r.fill("check", "text", read("saju_listing_bad.txt"))
            r.fill("check", "payments", "1")
            r.fill("check", "category", "fortune")
            r.run("raw", hold_after=6)
            r.fill("check", "text", read("saju_play_listing_live.txt"))
            r.fill("check", "accounts", "1")
            r.run("raw", hold_after=5)
            r.run("agent", hold_after=10)
            log["tab_check"] = r.close()
            print("tab_check", log["tab_check"]["events"][-2])

        if want("tab_refund"):
            r = Rec(pw, a.base, out, "tab_refund")
            r.tab("refund")
            r.fill("refund", "event", read("rc_refund_subscription.json"))
            r.fill("refund", "products", read("products.json"))
            r.fill("refund", "app_name", "Saju Today")
            r.run("agent", hold_after=9)
            log["tab_refund"] = r.close()
            print("tab_refund", log["tab_refund"]["events"][-2])

        if want("tab_notes"):
            r = Rec(pw, a.base, out, "tab_notes")
            r.tab("notes")
            r.fill("notes", "git_log", read("saju_1.1.17_gitlog.txt"))
            r.fill("notes", "version", "1.1.17")
            r.fill("notes", "include_internal", "1")  # this repo records shipped work under chore(session)
            r.run("agent", hold_after=12)
            log["tab_notes"] = r.close()
            print("tab_notes", log["tab_notes"]["events"][-2])

        if want("tab_listing"):
            r = Rec(pw, a.base, out, "tab_listing")
            r.tab("listing")
            r.fill("listing", "app_facts", read("app_facts_example.json"))
            r.fill("listing", "lang", "ko")
            r.run("agent", hold_after=14)
            log["tab_listing"] = r.close()
            print("tab_listing", log["tab_listing"]["events"][-2])

    (out / "events.json").write_text(json.dumps(log, indent=2, ensure_ascii=False))
    shutil.rmtree(out / "_raw", ignore_errors=True)
    print("wrote", out / "events.json")


if __name__ == "__main__":
    main()
