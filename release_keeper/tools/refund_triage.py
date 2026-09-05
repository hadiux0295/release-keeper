"""Tool 2 — refund_triage: classify a RevenueCat webhook event and draft the reply.

Input is the JSON body RevenueCat POSTs to a webhook (``{"api_version": "1.0",
"event": {...}}``) or the bare ``event`` object. Output is what a solo developer
needs to act in one minute: what happened, what to do to the user's entitlement,
whether a reply is needed, and a ready-to-edit reply draft.

Deterministic (no LLM inside). The event-type and cancel_reason vocabularies
are taken from RevenueCat's public webhook reference
(docs/integrations/webhooks/event-types-and-fields); unseen values fall into a
``manual_review`` bucket instead of raising.

Key rule (learned the hard way on the author's own app): RevenueCat has no
``REFUND`` event. A refund arrives as ``CANCELLATION`` with
``cancel_reason == "CUSTOMER_SUPPORT"`` for subscriptions *and* one-time
purchases. ``REFUND_REVERSED`` exists only for the App Store.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Any, Optional

from strands import tool

# --- RevenueCat vocab (public docs, fetched 2026-09-05) ----------------------

EVENT_TYPES = {
    "TEST", "INITIAL_PURCHASE", "RENEWAL", "CANCELLATION", "UNCANCELLATION",
    "NON_RENEWING_PURCHASE", "SUBSCRIPTION_PAUSED", "EXPIRATION", "BILLING_ISSUE",
    "PRODUCT_CHANGE", "SUBSCRIPTION_EXTENDED", "REFUND_REVERSED", "INVOICE_ISSUANCE",
    "TRANSFER", "TEMPORARY_ENTITLEMENT_GRANT", "VIRTUAL_CURRENCY_TRANSACTION",
    "EXPERIMENT_ENROLLMENT", "PURCHASE_REDEEMED", "SUBSCRIBER_ALIAS",
    "PRICE_INCREASE_CONSENT_REQUIRED", "PRICE_INCREASE_CONSENT_APPROVED",
}
CANCEL_REASONS = {"UNSUBSCRIBE", "BILLING_ERROR", "DEVELOPER_INITIATED", "PRICE_INCREASE", "CUSTOMER_SUPPORT", "UNKNOWN"}
GRANT_TYPES = {"INITIAL_PURCHASE", "RENEWAL", "NON_RENEWING_PURCHASE", "UNCANCELLATION", "PRODUCT_CHANGE",
               "SUBSCRIPTION_EXTENDED", "TEMPORARY_ENTITLEMENT_GRANT", "PURCHASE_REDEEMED"}
INFO_TYPES = {"TEST", "SUBSCRIPTION_PAUSED", "INVOICE_ISSUANCE", "VIRTUAL_CURRENCY_TRANSACTION",
              "EXPERIMENT_ENROLLMENT", "SUBSCRIBER_ALIAS", "PRICE_INCREASE_CONSENT_APPROVED"}

# Classes this tool emits. Each maps to an entitlement action + reply need.
#   refund            CANCELLATION + CUSTOMER_SUPPORT  → revoke now, reply (confirm)
#   refund_reversed   REFUND_REVERSED (App Store only) → restore, reply (confirm)
#   voluntary_cancel  CANCELLATION + UNSUBSCRIBE       → keep until expiration, optional win-back
#   billing_issue     BILLING_ISSUE / CANCELLATION+BILLING_ERROR → keep (grace), reply (update payment)
#   price_increase    CANCELLATION + PRICE_INCREASE / CONSENT_REQUIRED → keep until expiration, reply
#   developer_cancel  CANCELLATION + DEVELOPER_INITIATED → keep until expiration, no reply
#   ambiguous_cancel  CANCELLATION + UNKNOWN / missing  → no-op, manual look
#   expiration        EXPIRATION                        → revoke at expiry, no reply
#   grant             purchase/renewal family           → grant, no reply
#   transfer          TRANSFER                          → manual review
#   info              test / analytics events           → no-op
#   manual_review     anything unrecognised

REPLY_TEMPLATES = {
    "en": {
        "refund": (
            "Hi,\n\nYour refund for {product_label} has been processed through {store_label}. "
            "The amount goes back to your original payment method; the store usually takes 3–5 business days to post it.\n\n"
            "Access tied to that purchase has been removed from your account. If you did not request this refund, "
            "reply to this message and we will look into it.\n\nThank you for trying {app_name}."
        ),
        "refund_reversed": (
            "Hi,\n\nThe refund for {product_label} was reversed by {store_label}, so the purchase is active again "
            "and your access has been restored. Nothing further is needed on your side.\n\nThanks,\n{app_name}"
        ),
        "voluntary_cancel": (
            "Hi,\n\nWe've noted that your {product_label} subscription is set to cancel. You keep full access until "
            "{expires_label}; nothing more will be charged after that.\n\nIf something didn't work as expected, "
            "one line back about what was missing would genuinely help.\n\nThanks for using {app_name}."
        ),
        "billing_issue": (
            "Hi,\n\n{store_label} could not charge your payment method for {product_label}. Your access stays on "
            "during the grace period{grace_label}. To keep it, update the payment method in your {store_label} "
            "account settings — no action is needed inside the app.\n\nThanks,\n{app_name}"
        ),
        "price_increase": (
            "Hi,\n\nYour {product_label} subscription needs your consent to the updated price before it renews. "
            "Until then your current plan continues to {expires_label}. You can confirm or cancel from your "
            "{store_label} subscriptions page.\n\nThanks,\n{app_name}"
        ),
    },
    "ko": {
        "refund": (
            "안녕하세요.\n\n{product_label} 환불이 {store_label}를 통해 처리되었습니다. 환불 금액은 결제하신 수단으로 "
            "돌아가며, 스토어 반영까지 보통 3~5영업일이 걸립니다.\n\n해당 구매에 연결된 이용 권한은 계정에서 회수되었습니다. "
            "직접 요청하신 환불이 아니라면 이 메시지에 답장해 주세요. 확인해 드리겠습니다.\n\n{app_name}을 이용해 주셔서 감사합니다."
        ),
        "refund_reversed": (
            "안녕하세요.\n\n{store_label}에서 {product_label} 환불이 취소되어 구매가 다시 활성화되었고, 이용 권한도 복구되었습니다. "
            "추가로 하실 일은 없습니다.\n\n감사합니다.\n{app_name}"
        ),
        "voluntary_cancel": (
            "안녕하세요.\n\n{product_label} 구독 해지 예약을 확인했습니다. {expires_label}까지는 그대로 이용하실 수 있고, "
            "이후에는 추가 결제가 없습니다.\n\n기대와 달랐던 점이 있었다면 한 줄만 남겨 주셔도 큰 도움이 됩니다.\n\n{app_name}을 이용해 주셔서 감사합니다."
        ),
        "billing_issue": (
            "안녕하세요.\n\n{store_label}에서 {product_label} 결제가 승인되지 않았습니다. 유예 기간{grace_label} 동안은 이용 권한이 "
            "유지됩니다. 계속 이용하시려면 {store_label} 계정 설정에서 결제 수단을 갱신해 주세요. 앱 안에서 하실 일은 없습니다.\n\n감사합니다.\n{app_name}"
        ),
        "price_increase": (
            "안녕하세요.\n\n{product_label} 구독은 변경된 가격에 동의하셔야 갱신됩니다. 동의 전까지는 현재 요금제가 {expires_label}까지 "
            "유지됩니다. {store_label} 구독 관리 페이지에서 확인 또는 해지하실 수 있습니다.\n\n감사합니다.\n{app_name}"
        ),
    },
}

STORE_LABELS = {
    "PLAY_STORE": "Google Play", "APP_STORE": "the App Store", "MAC_APP_STORE": "the Mac App Store",
    "AMAZON": "Amazon Appstore", "STRIPE": "Stripe", "PADDLE": "Paddle", "RC_BILLING": "our web checkout",
    "ROKU": "Roku", "PROMOTIONAL": "a promotional grant", "TEST_STORE": "the test store",
}


@dataclass
class Triage:
    event_class: str
    event_type: str
    cancel_reason: Optional[str]
    product_id: Optional[str]
    product_kind: str  # subscription | consumable | unknown
    store: Optional[str]
    environment: Optional[str]
    app_user_id: Optional[str]
    entitlement_action: str
    reply_needed: bool
    reply_draft: Optional[str]
    facts: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    needs_input: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, indent=2)


# --- helpers -----------------------------------------------------------------

def _load(event_json: Any) -> dict:
    obj = json.loads(event_json) if isinstance(event_json, str) else dict(event_json)
    if isinstance(obj.get("event"), dict):  # full webhook body
        obj = obj["event"]
    return obj


def _ms_to_date(ms: Any) -> Optional[str]:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError):
        return None


def _base_product(product_id: Optional[str]) -> Optional[str]:
    # Play subscriptions arrive as "<product>:<base_plan>"; strip the base plan.
    return product_id.split(":", 1)[0] if product_id else None


def infer_product_kind(event: dict, products: dict) -> str:
    pid = event.get("product_id")
    base = _base_product(pid)
    for key in (pid, base):
        if key and key in products:
            return products[key].get("kind", "unknown")
    if event.get("type") == "NON_RENEWING_PURCHASE":
        return "consumable"
    if event.get("type") in ("INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION", "PRODUCT_CHANGE", "EXPIRATION",
                             "BILLING_ISSUE", "SUBSCRIPTION_PAUSED", "SUBSCRIPTION_EXTENDED"):
        return "subscription"
    if pid and ":" in pid:
        return "subscription"
    if pid and re.search(r"(credit|coin|pack|token|bundle|_\d+$)", pid, re.I):
        return "consumable"
    if pid and re.search(r"(month|year|week|annual|pro|premium|plus|sub)", pid, re.I):
        return "subscription"
    return "unknown"


def classify(event: dict) -> str:
    t = event.get("type")
    reason = event.get("cancel_reason")
    if t not in EVENT_TYPES:
        return "manual_review"
    if t == "CANCELLATION":
        if reason == "CUSTOMER_SUPPORT":
            return "refund"
        if reason == "UNSUBSCRIBE":
            return "voluntary_cancel"
        if reason == "BILLING_ERROR":
            return "billing_issue"
        if reason == "PRICE_INCREASE":
            return "price_increase"
        if reason == "DEVELOPER_INITIATED":
            return "developer_cancel"
        return "ambiguous_cancel"  # UNKNOWN, missing, or unlisted value
    if t == "REFUND_REVERSED":
        return "refund_reversed"
    if t == "BILLING_ISSUE":
        return "billing_issue"
    if t == "PRICE_INCREASE_CONSENT_REQUIRED":
        return "price_increase"
    if t == "EXPIRATION":
        return "expiration"
    if t == "TRANSFER":
        return "transfer"
    if t in GRANT_TYPES:
        return "grant"
    if t in INFO_TYPES:
        return "info"
    return "manual_review"


def entitlement_action(cls: str, kind: str, products: dict, event: dict) -> str:
    pid = event.get("product_id")
    grant = (products.get(pid) or products.get(_base_product(pid) or "") or {}).get("grant")
    what = f" ({grant})" if grant else ""
    if cls == "refund":
        if kind == "consumable":
            return f"deduct the granted quantity{what} now; allow the balance to go negative if already spent"
        if kind == "subscription":
            return "revoke the subscription entitlement now (do not wait for EXPIRATION; it will also arrive with a different event id)"
        return "revoke whatever this product granted; product kind unknown — confirm mapping"
    if cls == "refund_reversed":
        return "restore the entitlement/quantity removed by the earlier refund"
    if cls in ("voluntary_cancel", "developer_cancel", "price_increase"):
        return "no change now; access stays until expiration_at, then EXPIRATION revokes"
    if cls == "billing_issue":
        return "no change now; keep access through the grace period, revoke on EXPIRATION"
    if cls == "ambiguous_cancel":
        return "no change (cancel_reason missing/unknown); if the store shows a refund, treat as refund manually"
    if cls == "expiration":
        return "revoke the subscription entitlement (idempotent with any earlier refund)"
    if cls == "grant":
        return f"grant the product{what}; idempotent on event id"
    if cls == "transfer":
        return "move entitlements from transferred_from to transferred_to app_user_ids; review by hand"
    if cls == "info":
        return "none"
    return "none — unrecognised event; log the raw payload and review"


def draft_reply(cls: str, event: dict, *, app_name: str, language: str, product_label: str, store_label: str) -> Optional[str]:
    tpl = REPLY_TEMPLATES.get(language, REPLY_TEMPLATES["en"]).get(cls)
    if not tpl:
        return None
    exp = _ms_to_date(event.get("expiration_at_ms"))
    grace = _ms_to_date(event.get("grace_period_expiration_at_ms"))
    return tpl.format(
        product_label=product_label,
        store_label=store_label,
        app_name=app_name,
        expires_label=exp or ("the end of the current period" if language == "en" else "현재 이용 기간 종료"),
        grace_label=(f" (until {grace})" if language == "en" else f"({grace}까지)") if grace else "",
    )


def run_refund_triage(
    event_json: Any,
    *,
    products: Optional[dict] = None,
    app_name: str = "our app",
    language: str = "en",
) -> Triage:
    """Pure-python core (unit-testable without the agent)."""
    products = products or {}
    event = _load(event_json)
    cls = classify(event)
    kind = infer_product_kind(event, products)
    pid = event.get("product_id")
    store = event.get("store")
    env = event.get("environment")
    label_map = products.get(pid) or products.get(_base_product(pid) or "") or {}
    product_label = label_map.get("label") or _base_product(pid) or "your purchase"
    store_label = STORE_LABELS.get(store or "", store or "the store")

    t = Triage(
        event_class=cls,
        event_type=event.get("type") or "MISSING",
        cancel_reason=event.get("cancel_reason"),
        product_id=pid,
        product_kind=kind,
        store=store,
        environment=env,
        app_user_id=event.get("app_user_id"),
        entitlement_action=entitlement_action(cls, kind, products, event),
        reply_needed=cls in ("refund", "refund_reversed", "billing_issue", "price_increase"),
        reply_draft=draft_reply(cls, event, app_name=app_name, language=language,
                                product_label=product_label, store_label=store_label),
        facts={
            "event_id": event.get("id"),
            "purchased_at": _ms_to_date(event.get("purchased_at_ms")),
            "expiration_at": _ms_to_date(event.get("expiration_at_ms")),
            "price": event.get("price"),
            "currency": event.get("currency"),
            "period_type": event.get("period_type"),
            "country_code": event.get("country_code"),
        },
    )

    # warnings
    if env == "SANDBOX":
        t.warnings.append("SANDBOX event — do not touch production entitlements or email a real customer.")
    if cls == "voluntary_cancel":
        t.warnings.append("Reply is optional: a short win-back note is fine, a refund is not implied.")
    if cls == "refund" and store == "PLAY_STORE":
        t.warnings.append("Play refunds reach RevenueCat only if the Play service account (voided purchases) is registered in the RevenueCat dashboard.")
    if cls == "refund" and event.get("cancel_reason") and event["cancel_reason"] not in CANCEL_REASONS:
        t.warnings.append(f"cancel_reason '{event['cancel_reason']}' is not in the documented list.")
    if cls == "ambiguous_cancel":
        t.warnings.append("CANCELLATION without a documented cancel_reason: RevenueCat sends CUSTOMER_SUPPORT for refunds; anything else is not a refund.")
    if cls == "manual_review":
        t.warnings.append(f"Unrecognised event type '{t.event_type}'.")
    if not event.get("app_user_id") or str(event.get("app_user_id", "")).startswith("$RCAnonymousID:"):
        t.warnings.append("app_user_id is missing or anonymous — the entitlement change cannot be attributed to an account.")
    if kind == "unknown" and cls in ("refund", "grant", "refund_reversed"):
        t.needs_input.append(f"product_kind: is '{pid}' a subscription or a consumable? Pass a products map to pin it.")
    if t.reply_needed and not label_map.get("label"):
        t.needs_input.append("product_label: the reply uses the raw product id; pass products[<id>].label for a customer-facing name.")
    return t


@tool
def refund_triage(event_json: str, products_json: str = "{}", app_name: str = "our app", language: str = "en") -> str:
    """Classify a RevenueCat webhook event (refund, cancellation, billing issue, purchase, ...) and draft the customer reply.

    Returns JSON: event_class, entitlement_action (what to do to the user's access),
    reply_needed, reply_draft (ready to edit), facts, warnings, needs_input.
    Remember: RevenueCat sends refunds as CANCELLATION + cancel_reason=CUSTOMER_SUPPORT, never as a REFUND event.

    Args:
        event_json: The webhook body RevenueCat POSTed (full body with "event", or the bare event object) as a JSON string.
        products_json: Optional JSON map product_id -> {"kind": "subscription"|"consumable", "label": "...", "grant": "..."}.
        app_name: App name used in the reply draft.
        language: Reply language, "en" or "ko".
    """
    products = json.loads(products_json) if products_json else {}
    return run_refund_triage(event_json, products=products, app_name=app_name, language=language).to_json()
