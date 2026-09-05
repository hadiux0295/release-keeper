"""Tool 4 — release_notes: turn a ``git log`` into user-facing release notes.

Input is the plain text of ``git log --oneline`` (or ``--format=%s``) between the
previous release and HEAD. Output is a structured draft: user-facing entries
grouped New / Improved / Fixed, breaking items surfaced first, internal noise
dropped, plus a store-length "what's new" that respects the Play Store cap.

Deterministic (no LLM inside): parsing Conventional-Commit prefixes and a
keyword fallback for free-form subjects. Lines the rules cannot place are
returned under ``unclassified`` so the developer (or the agent) decides, rather
than silently dropped or silently shipped.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from typing import Optional

from strands import tool

PLAY_WHATS_NEW_MAX = 500      # Google Play "What's new" hard cap (chars)
APP_STORE_WHATS_NEW_MAX = 4000

CONVENTIONAL = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[^)]*)\))?(?P<bang>!)?:\s*(?P<subject>.+)$")
HASH_PREFIX = re.compile(r"^[0-9a-f]{7,40}\s+")

USER_FACING_TYPES = {"feat": "new", "fix": "fixed", "perf": "improved"}
INTERNAL_TYPES = {"chore", "ci", "build", "test", "docs", "style", "refactor", "revert", "release", "sync", "gen", "session"}

# `[tlog]` session-index commits are NOT noise: their subject is the only record of what shipped
# in repos that commit per work session (measured on the author's monorepo, 45/45 commits in a release range).
NOISE_PATTERNS = [
    r"\[oci-autocommit\]", r"^merge\b", r"^wip\b", r"^bump\b", r"^version\b",
    r"^release v?\d", r"^tlog\b", r"^update readme", r"^\s*$",
]
# keyword fallback for non-conventional subjects (EN + KO)
KW_FIXED = [r"\bfix(e[sd])?\b", r"\bbug\b", r"\bcrash", r"\bbroken\b", r"\bregression\b", r"\bhotfix\b",
            r"수정", r"버그", r"오류", r"고침", r"크래시", r"정정"]
KW_NEW = [r"\badd(s|ed)?\b", r"\bnew\b", r"\bintroduc", r"\blaunch", r"\bsupport for\b", r"\benable[sd]?\b",
          r"추가", r"신규", r"신설", r"도입", r"지원"]
KW_IMPROVED = [r"\bimprov", r"\bfaster\b", r"\bspeed", r"\bpolish", r"\bbetter\b", r"\brefine", r"\bupdate[sd]?\b",
               r"\bredesign", r"\brestyl", r"개선", r"향상", r"통일", r"정리", r"리디자인", r"교체"]
KW_INTERNAL = [r"\bdocs?\b", r"\breadme\b", r"\btests?\b", r"\bci\b", r"\blint", r"\brefactor", r"\bcleanup",
               r"\binternal\b", r"\bscaffold", r"\bworklog", r"\bhandoff", r"세션", r"검수", r"리포트", r"문서", r"기록",
               r"design_lab", r"샘플", r"검토", r"실측", r"진단"]
KW_BREAKING = [r"\bbreaking\b", r"\bremoved? support\b", r"\bdrop(s|ped)? support\b", r"\bmigrat", r"\brequires? (re-?login|reinstall)",
               r"호환 불가", r"재로그인", r"재설치"]


@dataclass
class Entry:
    text: str
    kind: str          # new | improved | fixed | breaking | internal | unclassified
    source: str        # original line
    scope: Optional[str] = None
    via: str = "conventional"  # conventional | keyword | none
    keyword_kind: Optional[str] = None  # for typed-internal commits: what the subject's keywords say


@dataclass
class Notes:
    version: Optional[str]
    breaking: list[Entry] = field(default_factory=list)
    new: list[Entry] = field(default_factory=list)
    improved: list[Entry] = field(default_factory=list)
    fixed: list[Entry] = field(default_factory=list)
    unclassified: list[Entry] = field(default_factory=list)
    dropped_internal: int = 0
    dropped_noise: int = 0
    markdown: str = ""
    store_whats_new: str = ""
    store_cap: int = PLAY_WHATS_NEW_MAX
    warnings: list[str] = field(default_factory=list)

    def to_agent_json(self) -> str:
        """Slim view for the model: the entry lists are already in `markdown`, so only
        unclassified keeps its source lines (the model must decide those). Cuts the tool
        payload roughly in half versus to_json()."""
        d = {
            "version": self.version,
            "counts": {k: len(getattr(self, k)) for k in ("breaking", "new", "improved", "fixed", "unclassified")},
            "dropped": {"internal": self.dropped_internal, "noise": self.dropped_noise},
            "draft_markdown": self.markdown,
            "draft_store_whats_new": self.store_whats_new,
            "store_cap": self.store_cap,
            "note": ("Both drafts are raw commit subjects grouped by rule — developer jargon, not user copy. "
                     "Rewrite every line for users and drop lines users would never notice "
                     "(reports, measurements, specs, design samples, deploy logs, tests, docs). Do not copy the drafts."),
            "unclassified": [e.source for e in self.unclassified],
            "warnings": self.warnings,
        }
        return json.dumps(d, ensure_ascii=False, indent=2)

    def to_json(self) -> str:
        d = asdict(self)
        d["counts"] = {k: len(getattr(self, k)) for k in ("breaking", "new", "improved", "fixed", "unclassified")}
        d["store_whats_new_chars"] = len(self.store_whats_new)
        return json.dumps(d, ensure_ascii=False, indent=2)


def _match_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def _clean_subject(s: str) -> str:
    s = re.sub(r"\s*\[(tlog|oci-autocommit|skip ci|ci skip)\]\s*", " ", s, flags=re.I)
    s = re.sub(r"\s*\(#\d+\)\s*$", "", s)            # trailing PR number
    s = re.sub(r"\s+", " ", s).strip(" .-—·")
    return s


def _humanize(subject: str) -> str:
    """Light rewrite: capitalise, strip developer shorthand. The model does the real prose pass."""
    s = subject.strip()
    s = re.sub(r"^v[A-Z]?\d+[\w.]*(\([^)]*\))?\s*[—:-]?\s*", "", s)   # leading version tag "vC19 —", "vC16(1.1.13) —"
    s = re.sub(r"^[A-Z]\d+(\([a-z]\))?(\+[A-Z]\d+(\([a-z]\))?)*\s*[—:-]?\s*", "", s)  # ticket-ish "M2+M3 —", "H2(b)+H1 —"
    s = s.strip(" -—:")
    return s[:1].upper() + s[1:] if s else s


def parse_line(raw: str) -> Optional[Entry]:
    line = raw.strip()
    if not line:
        return None
    line = HASH_PREFIX.sub("", line)
    if _match_any(line, NOISE_PATTERNS):
        return Entry(text="", kind="noise", source=raw.strip(), via="none")

    m = CONVENTIONAL.match(line)
    if m:
        ctype, scope, bang, subject = m.group("type"), m.group("scope"), m.group("bang"), _clean_subject(m.group("subject"))
        text = _humanize(subject)
        if bang or _match_any(subject, KW_BREAKING):
            return Entry(text, "breaking", raw.strip(), scope, "conventional")
        if ctype in USER_FACING_TYPES:
            return Entry(text, USER_FACING_TYPES[ctype], raw.strip(), scope, "conventional")
        if ctype in INTERNAL_TYPES:
            # A typed-internal commit whose subject still describes user-visible work (session-index
            # style "chore(session): fixed X, shipped Y") gets a keyword pass; the caller decides via
            # include_internal whether these are kept at all.
            kw = _keyword_kind(subject)
            return Entry(text, "internal", raw.strip(), scope, "conventional", keyword_kind=kw)
        return Entry(text, "unclassified", raw.strip(), scope, "conventional")

    subject = _clean_subject(line)
    text = _humanize(subject)
    kind = _keyword_kind(subject)
    return Entry(text, kind or "unclassified", raw.strip(), None, "keyword" if kind else "none")


def _keyword_kind(subject: str) -> Optional[str]:
    """breaking | internal | fixed | new | improved from the EN/KO keyword lists, or None."""
    if _match_any(subject, KW_BREAKING):
        return "breaking"
    if _match_any(subject, KW_INTERNAL) and not _match_any(subject, KW_FIXED):
        return "internal"
    for kind, kws in (("fixed", KW_FIXED), ("new", KW_NEW), ("improved", KW_IMPROVED)):
        if _match_any(subject, kws):
            return kind
    return None


def render_markdown(n: Notes, language: str) -> str:
    h = {"en": ("Important", "New", "Improved", "Fixed"), "ko": ("중요", "새 기능", "개선", "수정")}[language]
    out = [f"## {n.version or 'Unreleased'}", ""]
    for title, items in zip(h, (n.breaking, n.new, n.improved, n.fixed)):
        if items:
            out.append(f"### {title}")
            out.extend(f"- {e.text}" for e in items)
            out.append("")
    return "\n".join(out).rstrip() + "\n"


def render_store(n: Notes, language: str, limit: int) -> str:
    """Short 'what's new': breaking first, then new, improved, fixed; stops before the cap."""
    prefix = {"en": ("Important: ", "New: ", "Improved: ", "Fixed: "), "ko": ("중요: ", "새 기능: ", "개선: ", "수정: ")}[language]
    lines: list[str] = []
    for pre, items in zip(prefix, (n.breaking, n.new, n.improved, n.fixed)):
        for e in items:
            cand = f"• {pre}{e.text}"
            if len("\n".join(lines + [cand])) > limit:
                return "\n".join(lines)
            lines.append(cand)
    return "\n".join(lines)


def run_release_notes(
    git_log: str,
    *,
    version: Optional[str] = None,
    language: str = "en",
    store: str = "play",
    include_internal: bool = False,
) -> Notes:
    """Pure-python core (unit-testable without the agent)."""
    language = language if language in ("en", "ko") else "en"
    limit = PLAY_WHATS_NEW_MAX if store == "play" else APP_STORE_WHATS_NEW_MAX
    n = Notes(version=version, store_cap=limit)
    seen: set[str] = set()
    for raw in git_log.splitlines():
        e = parse_line(raw)
        if e is None:
            continue
        if e.kind == "noise":
            n.dropped_noise += 1
            continue
        if e.kind == "internal" and not include_internal:
            n.dropped_internal += 1
            continue
        key = e.text.lower()
        if key in seen:
            continue
        seen.add(key)
        if e.kind == "internal":                      # kept only with include_internal
            if e.keyword_kind in ("breaking", "new", "improved", "fixed"):
                e.kind, e.via = e.keyword_kind, "keyword"
            else:
                e.kind = "unclassified"
        getattr(n, e.kind).append(e)

    n.markdown = render_markdown(n, language)
    n.store_whats_new = render_store(n, language, limit)
    if not (n.breaking or n.new or n.improved or n.fixed):
        hint = (f" {n.dropped_internal} typed-internal commit(s) were dropped — re-run with include_internal=true if your "
                "repo records shipped work under chore/docs (session-index style)." if n.dropped_internal else "")
        n.warnings.append("No user-facing entries found. Either the range is wrong or commits are not typed (feat/fix/perf); check unclassified." + hint)
    if n.unclassified:
        n.warnings.append(f"{len(n.unclassified)} commit(s) could not be classified — decide whether each is user-facing.")
    total = sum(len(getattr(n, k)) for k in ("breaking", "new", "improved", "fixed"))
    shown = n.store_whats_new.count("•")
    if total and shown < total:
        n.warnings.append(f"Store text truncated to {shown}/{total} entries to stay under {limit} chars.")
    if n.breaking:
        n.warnings.append("Breaking/migration items present — say what the user must do, not only what changed.")
    return n


@tool
def release_notes(git_log: str, version: str = "", language: str = "en", store: str = "play", include_internal: bool = False) -> str:
    """Turn raw `git log --oneline` text into user-facing release notes and a store 'what's new' block.

    Returns JSON: breaking/new/improved/fixed entries, unclassified (needs a human call),
    dropped counts, markdown, store_whats_new (Play cap 500 chars / App Store 4000), warnings.
    The wording is a draft — rewrite entries in plain user language before publishing.

    Args:
        git_log: Output of `git log --oneline <prev>..HEAD` (one commit per line; hash optional).
        version: Version label for the heading, e.g. "1.2.0".
        language: "en" or "ko" for headings and store prefixes.
        store: "play" or "appstore" — selects the what's-new character cap.
        include_internal: Keep chore/docs/test commits in unclassified instead of dropping them.
    """
    n = run_release_notes(git_log, version=version or None, language=language, store=store,
                          include_internal=include_internal)
    return n.to_agent_json()
