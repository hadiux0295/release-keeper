"""CLI.

  python -m release_keeper check  <copy.txt> [--payments] [--accounts] [--emotional] [--category fortune] [--raw]
  python -m release_keeper refund <event.json> [--products products.json] [--app-name X] [--lang en|ko] [--raw]
  python -m release_keeper notes  [<gitlog.txt> | --repo PATH --since TAG] [--version V] [--lang en|ko] [--store play|appstore] [--raw]
  python -m release_keeper listing <app_facts.json> [--lang ko] [--store play|appstore] [--check <listing.json>] [--raw]

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


def _run_agent(prompt: str) -> str:
    from .agent import run_agent
    text, meta = run_agent(prompt)
    print(text)
    print(f"\n[{meta['seconds']:.1f}s · in {meta['input_tokens']} / out {meta['output_tokens']} tokens · {meta['tool_calls']} tool call(s)]",
          file=sys.stderr)
    return text


def cmd_check(a) -> int:
    text = Path(a.file).read_text(encoding="utf-8")
    if a.raw:
        from .tools import run_disclosure_check
        print(run_disclosure_check(text, has_payments=a.payments, has_accounts=a.accounts,
                                   has_free_text_emotional_input=a.emotional, category=a.category).to_json())
        return 0
    flags = (f"app_uses_ai=true, has_payments={a.payments}, has_accounts={a.accounts}, "
             f"has_free_text_emotional_input={a.emotional}, category={a.category}")
    _run_agent(f"Run disclosure_check on the copy below with {flags}, then summarize.\n\n---\n{text}")
    return 0


def cmd_refund(a) -> int:
    event_json = Path(a.file).read_text(encoding="utf-8")
    products_json = Path(a.products).read_text(encoding="utf-8") if a.products else "{}"
    if a.raw:
        from .tools import run_refund_triage
        print(run_refund_triage(event_json, products=json.loads(products_json), app_name=a.app_name, language=a.lang).to_json())
        return 0
    _run_agent(
        f"Run refund_triage on the RevenueCat webhook event below (app_name={a.app_name!r}, language={a.lang!r}, "
        f"products_json={products_json!r}), then tell me what to do.\n\n---\n{event_json}")
    return 0


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
    _run_agent(
        f"Run release_notes on the git log below (version={a.version!r}, language={a.lang!r}, store={a.store!r}, "
        f"include_internal={a.include_internal}), then present the notes.\n\n---\n{log}")
    return 0


def cmd_listing(a) -> int:
    from .tools import run_listing_brief, run_listing_check
    facts = Path(a.file).read_text(encoding="utf-8")
    if a.check:
        listing = Path(a.check).read_text(encoding="utf-8")
        print(run_listing_check(listing, facts, language=a.lang, store=a.store).to_json())
        return 0
    if a.raw:
        print(run_listing_brief(facts, language=a.lang, store=a.store).to_json())
        return 0
    answer = _run_agent(
        f"Write the {a.store} store listing in language {a.lang!r} for the app described below. "
        f"Call listing_brief first (language={a.lang!r}, store={a.store!r}), write the listing, then call listing_check on it.\n\n---\n{facts}")
    # Independent verification: whatever the model claims, the deterministic check has the last word.
    from .agent import listing_post_check
    listing, rep = listing_post_check(answer, facts, language=a.lang, store=a.store)
    if listing is None:
        print("\n[post-check] no fenced JSON listing found in the answer — cannot verify", file=sys.stderr)
        return 2
    print("\n[post-check] " + rep.to_json(), file=sys.stderr)
    return 0 if rep.verdict != "BLOCK" else 1


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

    l = sub.add_parser("listing", help="brief → model writes → check; or --check a listing JSON")
    l.add_argument("file", help="app facts JSON (see examples/app_facts_example.json)")
    l.add_argument("--lang", default="en")
    l.add_argument("--store", default="play", choices=["play", "appstore"])
    l.add_argument("--check", help="listing JSON to validate deterministically (no LLM)")
    l.add_argument("--raw", action="store_true", help="print the brief only (no LLM)")
    l.set_defaults(fn=cmd_listing)

    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
