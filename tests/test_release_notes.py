import json
from pathlib import Path

from release_keeper.tools import run_release_notes, release_notes
from release_keeper.tools.release_notes import PLAY_WHATS_NEW_MAX

EX = Path(__file__).resolve().parent.parent / "examples"
LOG = (EX / "gitlog_sample.txt").read_text(encoding="utf-8")


def test_noise_and_internal_dropped():
    n = run_release_notes(LOG, version="1.2.0")
    srcs = " ".join(e.source for b in (n.breaking, n.new, n.improved, n.fixed, n.unclassified) for e in b)
    assert "[oci-autocommit]" not in srcs and "[tlog]" not in srcs and "Merge branch" not in srcs
    assert "chore(session)" not in srcs and "docs(saju)" not in srcs and "test(saju)" not in srcs
    assert n.dropped_noise >= 3 and n.dropped_internal >= 5


def test_conventional_types_grouped():
    n = run_release_notes(LOG, version="1.2.0")
    assert any("re-login" in e.text for e in n.breaking)
    assert any(e.source.startswith("d4e5f6a") for e in n.improved)          # perf → improved
    assert any(e.source.startswith("47a0467") for e in n.new)              # feat → new
    assert any(e.source.startswith("c7bf446") for e in n.fixed)            # fix → fixed
    assert "### Important" in n.markdown and n.markdown.index("### Important") < n.markdown.index("### New")


def test_keyword_fallback_en_ko_and_unclassified():
    n = run_release_notes(LOG)
    assert any(e.via == "keyword" and "crash" in e.text.lower() for e in n.fixed)
    assert any(e.text.startswith("Add Japanese") for e in n.new)
    assert any("contrast" in e.text for e in n.improved)
    assert any("오류 수정" in e.text for e in n.fixed)
    assert any("tweak spacing" in e.text.lower() for e in n.unclassified)
    assert any("could not be classified" in w for w in n.warnings)


def test_store_text_respects_play_cap_and_reports_truncation():
    n = run_release_notes(LOG, store="play")
    assert len(n.store_whats_new) <= PLAY_WHATS_NEW_MAX
    total = len(n.breaking) + len(n.new) + len(n.improved) + len(n.fixed)
    shown = n.store_whats_new.count("•")
    assert shown >= 1
    if shown < total:
        assert any("truncated" in w for w in n.warnings)
    big = run_release_notes(LOG, store="appstore")
    assert big.store_whats_new.count("•") >= shown


def test_korean_headings_and_empty_log():
    k = run_release_notes("feat: 새 위젯 추가\nfix: 저장 오류 수정", language="ko", version="1.0")
    assert "### 새 기능" in k.markdown and "### 수정" in k.markdown and "• 새 기능: " in k.store_whats_new
    e = run_release_notes("chore: bump\n[tlog]\n")
    assert any("No user-facing" in w for w in e.warnings)


def test_tool_wrapper_json():
    out = json.loads(release_notes(LOG, version="1.2.0"))
    assert out["counts"]["fixed"] >= 3 and out["store_whats_new_chars"] <= PLAY_WHATS_NEW_MAX
