"""Render docs/architecture.mmd to docs/architecture.png with headless Chromium + mermaid (CDN).

Devpost and some renderers do not draw mermaid, so the README links the PNG as a fallback.
Needs `playwright` with chromium installed; no key or environment value is used.
"""
from __future__ import annotations

import pathlib
import sys

from playwright.sync_api import sync_playwright

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "docs" / "architecture.mmd"
OUT = ROOT / "docs" / "architecture.png"
MERMAID = "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"


def main() -> int:
    src = SRC.read_text(encoding="utf-8")
    html = f"""<!doctype html><html><head><meta charset=utf-8><script src="{MERMAID}"></script>
<style>body{{margin:0;background:#fff;font-family:'Segoe UI',Helvetica,Arial,sans-serif}}
#d{{display:inline-block;padding:28px;background:#fff}}</style></head>
<body><div id="d"><pre class="mermaid">{src}</pre></div>
<script>mermaid.initialize({{startOnLoad:true,theme:'neutral',themeVariables:{{fontSize:'18px'}},
flowchart:{{useMaxWidth:false,curve:'basis',padding:14,nodeSpacing:34,rankSpacing:60}}}});</script></body></html>"""
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        pg = b.new_page(viewport={"width": 2400, "height": 1400}, device_scale_factor=2)
        pg.set_content(html)
        pg.wait_for_selector("svg", timeout=30000)
        pg.wait_for_timeout(600)
        err = pg.query_selector("text=Syntax error")
        if err:
            print("mermaid syntax error", file=sys.stderr)
            return 1
        el = pg.query_selector("#d")
        el.screenshot(path=str(OUT))
        print(OUT, el.bounding_box())
        b.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
