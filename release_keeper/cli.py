"""CLI: python -m release_keeper check <copy.txt> [--payments] [--accounts] [--emotional] [--category fortune] [--raw]"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path


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
    a = p.parse_args(argv)

    text = Path(a.file).read_text(encoding="utf-8")
    if a.raw:
        from .tools import run_disclosure_check
        print(run_disclosure_check(text, has_payments=a.payments, has_accounts=a.accounts,
                                   has_free_text_emotional_input=a.emotional, category=a.category).to_json())
        return 0

    from .agent import build_agent
    agent = build_agent()
    flags = (f"app_uses_ai=true, has_payments={a.payments}, has_accounts={a.accounts}, "
             f"has_free_text_emotional_input={a.emotional}, category={a.category}")
    t0 = time.time()
    result = agent(f"Run disclosure_check on the copy below with {flags}, then summarize.\n\n---\n{text}")
    print(result)
    print(f"\n[{time.time()-t0:.1f}s]", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
