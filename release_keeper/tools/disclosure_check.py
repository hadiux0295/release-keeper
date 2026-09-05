"""Tool 1 — disclosure_check: rule-based compliance scan of user-facing app copy.

Scans store-listing / result-screen / ToS text for the disclosures an AI app
needs before release. Deterministic (no LLM inside) so results are reproducible
and cheap; the agent decides what to do with the findings.

Every finding is a *risk flag*, never a legal verdict. Items that cannot be
judged from text alone are returned under ``needs_input``.

Checklist adapted from the author's pre-existing internal review checklist
(see README "Reused material").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from typing import Optional

from strands import tool

# --- vocab -----------------------------------------------------------------

MODEL_NAMES = [
    "gemini", "gpt", "chatgpt", "openai", "claude", "anthropic", "llama", "mistral",
    "qwen", "nemotron", "deepseek", "gemma", "phi", "grok", "cohere", "bedrock",
    "nova", "titan",
]
AI_WORDS = ["ai", "artificial intelligence", "language model", "llm", "machine learning", "인공지능"]
GENERATED_WORDS = ["generat", "produced by", "written by", "created by", "powered by", "생성"]
REFLECTION_WORDS = ["reflection", "entertainment", "perspective", "explor", "philosoph", "재미", "참고", "엔터테인먼트"]
CERTAINTY_PATTERNS = [
    r"\baccurate prediction", r"\bpredicts? (your|the) future", r"\bguarantee", r"\bwill happen",
    r"\bexpert (reading|advice)", r"\bdiagnos", r"\bprofessional advice", r"\b100%",
    r"\btrue destiny", r"\bknow (exactly )?what (will|is going to)",
    # Korean: skip the negated forms used in disclaimers ("예언이 아닙니다", "보장하지 않습니다")
    r"예언(?![^.]{0,30}(아니|아닙|아님|않))", r"정확한 예측(?![^.]{0,30}(아니|아닙|아님|않))", r"반드시", r"보장(?![^.]{0,8}(않|아니|아닙))",
]
ADVICE_PATTERN = r"\b(medical|financial|legal|psychological|investment|health)\s+advice\b"
NOT_ADVICE_PATTERNS = [
    r"not (a substitute for|intended as|meant as)?\s*(professional\s+)?(medical|legal|financial|psychological|health)",
    r"(medical|legal|financial|psychological)[^.]{0,60}(not|never)[^.]{0,20}advice",
    r"no(t)?\s+(medical|legal|financial|psychological)",
    r"의학[^.]{0,20}조언[^.]{0,10}(아님|아닙니다)",
]
REFUND_WORDS = ["refund", "cancel", "환불", "취소"]
DELETE_WORDS = ["delete your account", "delete account", "account deletion", "delete your data", "erase your data", "계정 삭제", "데이터 삭제"]
CRISIS_WORDS = ["helpline", "findahelpline", "crisis", "emergency services", "988", "1393", "상담전화", "긴급"]
AGE_PATTERNS = [r"\b1[3-8]\+", r"\b(at least|over|older than|minimum age)\s+(of\s+)?1[3-8]\b", r"\b1[3-8]\s+(years|or older)", r"만\s?1[3-9]세"]
RENEW_WORDS = ["auto-renew", "automatically renew", "renews automatically", "recurring", "자동 갱신", "자동으로 갱신"]
SUBSCRIPTION_WORDS = ["subscription", "/month", "per month", "monthly", "/year", "yearly", "구독", "월 "]


@dataclass
class Finding:
    severity: str  # "red" | "yellow" | "green"
    item: str
    why: str
    fix: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    needs_input: list[str] = field(default_factory=list)
    passed: list[str] = field(default_factory=list)

    def add(self, sev: str, item: str, why: str, fix: str) -> None:
        self.findings.append(Finding(sev, item, why, fix))

    def to_json(self) -> str:
        order = {"red": 0, "yellow": 1, "green": 2}
        self.findings.sort(key=lambda f: order[f.severity])
        d = asdict(self)
        d["summary"] = {
            "red": sum(f.severity == "red" for f in self.findings),
            "yellow": sum(f.severity == "yellow" for f in self.findings),
            "green": sum(f.severity == "green" for f in self.findings),
            "verdict": "BLOCK" if any(f.severity == "red" for f in self.findings) else "SHIP_WITH_FIXES" if self.findings else "OK",
        }
        return json.dumps(d, ensure_ascii=False, indent=2)


def _any(text: str, words: list[str]) -> bool:
    return any(w in text for w in words)


def _anyre(text: str, patterns: list[str]) -> Optional[str]:
    for p in patterns:
        m = re.search(p, text, re.I)
        if m:
            return m.group(0)
    return None


def run_disclosure_check(
    text: str,
    *,
    app_uses_ai: bool = True,
    has_payments: bool = False,
    has_accounts: bool = False,
    has_free_text_emotional_input: bool = False,
    category: str = "general",
) -> Report:
    """Pure-python core (unit-testable without the agent)."""
    t = text.lower()
    r = Report()

    # 1. AI-output disclosure, with model/vendor name
    if app_uses_ai:
        has_ai = _any(t, AI_WORDS) and _any(t, GENERATED_WORDS)
        named = [m for m in MODEL_NAMES if re.search(rf"\b{m}\b", t)]
        if not has_ai:
            r.add("red", "ai_disclosure",
                  "Copy never states that output is AI-generated. Users and store reviewers cannot tell a model wrote it.",
                  "Add one visible sentence on the result screen and listing, e.g. "
                  "\"Every result is generated by an AI language model (<vendor model>).\"")
        elif not named:
            r.add("yellow", "ai_model_named",
                  "AI is mentioned but no model/vendor is named; transparency reads as vague.",
                  "Name the model in the disclosure, e.g. \"generated by an AI language model (Google Gemini)\".")
        else:
            r.passed.append(f"ai_disclosure (model named: {', '.join(named)})")

    # 2. Certainty / professional-authority language
    hit = _anyre(t, CERTAINTY_PATTERNS)
    if hit:
        r.add("red", "overstated_claim",
              f"Phrase \"{hit}\" implies predictive accuracy or professional authority; invites refund disputes and store rejection.",
              "Rewrite toward reflection framing: \"perspectives\", \"for reflection and entertainment\", \"explore\".")
    adv = re.search(ADVICE_PATTERN, t, re.I)
    if adv and not _anyre(t, NOT_ADVICE_PATTERNS):
        r.add("red", "advice_wording",
              f"Copy offers \"{adv.group(0)}\" without disclaiming it; may read as regulated professional advice.",
              "Remove the advice claim or add: \"not medical, psychological, legal, or financial advice\".")

    # 3. Reflection/entertainment framing + explicit not-advice statement (interpretation apps)
    if category in ("fortune", "interpretation", "wellness", "general") and app_uses_ai:
        framed = _any(t, REFLECTION_WORDS)
        not_advice = _anyre(t, NOT_ADVICE_PATTERNS) is not None
        if category in ("fortune", "interpretation", "wellness"):
            if not framed:
                r.add("red", "entertainment_framing",
                      "No reflection/entertainment framing; readers may take output as guidance.",
                      "State the purpose: \"for reflection and entertainment — not a prediction of the future\".")
            else:
                r.passed.append("entertainment_framing")
            if not not_advice:
                r.add("red", "not_advice_statement",
                      "Missing explicit \"not medical/psychological/legal/financial advice\" statement.",
                      "Add verbatim: \"not medical, psychological, legal, or financial advice\" next to the AI disclosure.")
            else:
                r.passed.append("not_advice_statement")
        elif not not_advice:
            r.add("green", "not_advice_statement",
                  "General AI app with no not-advice statement; low risk unless output touches health/money/law.",
                  "Consider a one-line disclaimer if output can be read as guidance.")

    # 4. Refund / cancellation / auto-renew
    if has_payments:
        if not _any(t, REFUND_WORDS):
            r.add("yellow", "refund_clarity",
                  "Nothing about refunds or cancellation is stated before purchase; chargebacks rise when this is hidden.",
                  "Add a pre-checkout line: refund window (or explicit no-refund) and how to cancel (self-service preferred).")
        else:
            r.passed.append("refund_clarity")
        if _any(t, SUBSCRIPTION_WORDS) and not _any(t, RENEW_WORDS):
            r.add("yellow", "auto_renew_disclosure",
                  "Subscription is mentioned but auto-renewal is not; stores require renewal terms before checkout.",
                  "State: \"Renews automatically at $X/month until cancelled. Cancel anytime in Settings.\"")

    # 5. Account / data deletion
    if has_accounts:
        if not _any(t, DELETE_WORDS):
            r.add("yellow", "account_deletion",
                  "No in-app account/data deletion path is described; Apple and Google require one.",
                  "Add \"Delete account\" in Settings that removes both auth user and stored data, and say so in the policy.")
        else:
            r.passed.append("account_deletion")

    # 6. Crisis referral for emotional free-text input
    if has_free_text_emotional_input:
        if not _any(t, CRISIS_WORDS):
            r.add("red", "crisis_referral",
                  "App accepts emotional/crisis free text but shows no helpline referral.",
                  "Add one line near the input: \"If you are in crisis, please contact a local helpline (findahelpline.com).\"")
        else:
            r.passed.append("crisis_referral")

    # 7. Minimum age
    if has_accounts or has_payments:
        if not _anyre(t, AGE_PATTERNS):
            r.add("yellow", "minimum_age",
                  "No stated minimum age; a lightweight self-attestation is the normal baseline.",
                  "State \"You must be 13 or older\" (or 18+ for paid tiers) at signup/checkout.")
        else:
            r.passed.append("minimum_age")

    # Items not decidable from copy
    r.needs_input.append("training_data_legality: was any model fine-tuned or prompted with crawled/copyrighted data? (not visible in copy)")
    if has_accounts:
        r.needs_input.append("deletion_completeness: does 'delete account' remove database records, not only the login? (needs code/infra check)")
    if has_payments:
        r.needs_input.append("jurisdiction: which consumer-protection rules apply (EU 14-day withdrawal, etc.) depends on user geography")
    return r


@tool
def disclosure_check(
    text: str,
    app_uses_ai: bool = True,
    has_payments: bool = False,
    has_accounts: bool = False,
    has_free_text_emotional_input: bool = False,
    category: str = "general",
) -> str:
    """Scan user-facing app copy (store listing, result screen, ToS) for required disclosures.

    Returns a JSON report: findings[{severity: red|yellow|green, item, why, fix}],
    passed[], needs_input[], summary{verdict: BLOCK|SHIP_WITH_FIXES|OK}.
    Red = blocking before release. Findings are risk flags, not legal verdicts.

    Args:
        text: The copy to scan (any language; English and Korean vocab built in).
        app_uses_ai: Whether the app shows AI-generated output to users.
        has_payments: Whether the app sells anything (one-time or subscription).
        has_accounts: Whether users create accounts / data is stored per user.
        has_free_text_emotional_input: Whether users can type free-form emotional or personal-crisis text.
        category: One of general | fortune | interpretation | wellness | game | productivity.
    """
    return run_disclosure_check(
        text,
        app_uses_ai=app_uses_ai,
        has_payments=has_payments,
        has_accounts=has_accounts,
        has_free_text_emotional_input=has_free_text_emotional_input,
        category=category,
    ).to_json()
