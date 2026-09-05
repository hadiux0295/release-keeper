"""CLI.

  python -m release_keeper check  <copy.txt> [--payments] [--accounts] [--emotional] [--category fortune] [--raw]
  python -m release_keeper refund <event.json> [--products products.json] [--app-name X] [--lang en|ko] [--raw]
  python -m release_keeper notes  [<gitlog.txt> | --repo PATH --since TAG] [--version V] [--lang en|ko] [--store play|appstore] [--raw]

--raw runs the deterministic tool directly (no LLM). Without it the Strands agent
plans the call and writes the summary.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


def _run_agent(prompt: str) -> int:
    from .agent import build_agent
    agent = build_agent()
    t0 = time.time()
    result = agent(prompt)
    print(result)
    print(f"\n[{time.time()-t0:.1f}s]", file=sys.stderr)
    return 0


def cmd_check(a) -> int:
    text = Path(a.file).read_text(encoding="utf-8")
    if a.raw:
        from .tools import run_disclosure_check
        print(run_disclosure_check(text, has_payments=a.payments, has_accounts=a.accounts,
                                   has_free_text_emotional_input=a.emotional, category=a.category).to_json())
        return 0
    flags = (f"app_uses_ai=true, has_payments={a.payments}, has_accounts={a.accounts}, "
             f"has_free_text_emotional_input={a.emotional}, category={a.category}")
    return _run_agent(f"Run disclosure_check on the copy below with {flags}, then summarize.\n\n---\n{text}")


def cmd_refund(a) -> int:
    event_json = Path(a.file).read_text(encoding="utf-8")
    products_json = Path(a.products).read_text(encoding="utf-8") if a.products else "{}"
    if a.raw:
        from .tools import run_refund_triage
        print(run_refund_triage(event_json, products=json.loads(products_json), app_name=a.app_name, language=a.lang).to_json())
        return 0
    return _run_agent(
        f"Run refund_triage on the RevenueCat webhook event below (app_name={a.app_name!r}, language={a.lang!r}, "
        f"products_json={products_json!r}), then tell me what to do.\n\n---\n{event_json}")


def cmd_notes(a) -> int:
    if a.file:
        log = Path(a.file).read_text(encoding="utf-8")
    else:
        rng = f"{a.since}..HEAD" if a.since else "HEAD"
        cmd = ["git", "-C", a.repo or ".", "log", "--oneline", "--no-merges", rng]
        if a.path:
            cmd += ["--", a.path]
        log = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    if a.raw:
        from .tools import run_release_notes
        print(run_release_notes(log, version=a.version, language=a.lang, store=a.store, include_internal=a.include_internal).to_json())
        return 0
    return _run_agent(
        f"Run release_notes on the git log below (version={a.version!r}, language={a.lang!r}, store={a.store!r}, "
        f"include_internal={a.include_internal}), then present the notes.\n\n---\n{log}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="release_keeper")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="scan app copy for required disclosures")
    c.add_argument("file", help="text file with listing / result-screen / ToS copy")
    c.add_argument("--payments", action="store_true")
    c.add_argument("--accounts", action="store_true")
    c.add_argument("--emotional", action="store_true", help="app accepts free-text emotional input")
    c.add_argument("--category", default="general")
    c.add_argument("--raw", action="store_true", help="run the tool directly, no LLM")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("refund", help="classify a RevenueCat webhook event and draft the reply")
    r.add_argument("file", help="JSON file with the webhook body (or bare event)")
    r.add_argument("--products", help="JSON map product_id -> {kind,label,grant}")
    r.add_argument("--app-name", default="our app")
    r.add_argument("--lang", default="en", choices=["en", "ko"])
    r.add_argument("--raw", action="store_true")
    r.set_defaults(fn=cmd_refund)

    n = sub.add_parser("notes", help="release notes from git log")
    n.add_argument("file", nargs="?", help="text file with `git log --oneline` output (omit to run git)")
    n.add_argument("--repo", help="repository path when running git")
    n.add_argument("--since", help="previous release tag/commit when running git")
    n.add_argument("--path", help="limit git log to a subdirectory")
    n.add_argument("--version", default=None)
    n.add_argument("--lang", default="en", choices=["en", "ko"])
    n.add_argument("--store", default="play", choices=["play", "appstore"])
    n.add_argument("--include-internal", action="store_true")
    n.add_argument("--raw", action="store_true")
    n.set_defaults(fn=cmd_notes)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
