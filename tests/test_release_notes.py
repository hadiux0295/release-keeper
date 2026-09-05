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
    assert n.dropped_noise >= 2 and n.dropped_internal >= 5


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


def test_agent_json_is_slim_but_keeps_unclassified_sources():
    n = run_release_notes(LOG, version="1.2.0")
    slim = json.loads(n.to_agent_json())
    full = json.loads(n.to_json())
    assert "new" not in slim and "fixed" not in slim and slim["draft_markdown"] == full["markdown"]
    assert len(slim["unclassified"]) == len(full["unclassified"]) and all(isinstance(u, str) for u in slim["unclassified"])
    assert len(n.to_agent_json()) < 0.6 * len(n.to_json())


SESSION_LOG = """088b7e11 chore(session): saju vC20(1.1.17) 빌드 + Play 내부 트랙 업로드 완료 [tlog]
7a3ced35 chore(session): saju 한영전환 답변언어 버그: dailyLine lang 오표기 수정·테스트 129/129 [tlog]
f70b9130 chore(session): saju 게스트 '체험 종료' CTA + Google 버튼 공식 다크변형 추가 [tlog]
2b869fa3 chore(gen): tlog 2026-09-05T07:08Z [oci-autocommit]
"""


def test_session_index_repo_default_warns_and_include_internal_classifies():
    """Repos that commit per work session: every commit is chore(session) … [tlog]. Default run must
    say so (not silently produce nothing); include_internal must keyword-classify the subjects."""
    n = run_release_notes(SESSION_LOG, version="1.1.17")
    assert not (n.new or n.fixed or n.improved) and n.dropped_internal == 3 and n.dropped_noise == 1
    assert any("include_internal" in w for w in n.warnings)
    k = run_release_notes(SESSION_LOG, version="1.1.17", include_internal=True)
    assert any("버그" in e.text for e in k.fixed) and any("CTA" in e.text for e in k.new)
    assert k.dropped_noise == 1                       # [oci-autocommit] is still noise
    assert json.loads(k.to_agent_json())["store_cap"] == PLAY_WHATS_NEW_MAX


def test_notes_post_check_measures_last_fenced_block():
    from release_keeper.agent import notes_post_check
    ans = "## 1.1.17\n- x\n\n```text\n• Fixed: language switch bug\n• New: guest exit button\n```\n"
    pc = notes_post_check(ans, 500)
    assert pc["ok"] and pc["chars"] == len("• Fixed: language switch bug\n• New: guest exit button")
    assert notes_post_check("no block here", 500)["block"] is None
    assert not notes_post_check("```\n" + "x" * 501 + "\n```", 500)["ok"]
