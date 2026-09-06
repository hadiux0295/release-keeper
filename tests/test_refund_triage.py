import json
from pathlib import Path

from release_keeper.tools import run_refund_triage, refund_triage

EX = Path(__file__).resolve().parent.parent / "examples"
PRODUCTS = json.loads((EX / "products.json").read_text())


def _ev(**kw):
    base = {"id": "evt_t", "type": "CANCELLATION", "app_user_id": "u1", "product_id": "credits_5",
            "store": "PLAY_STORE", "environment": "PRODUCTION"}
    base.update(kw)
    return {"api_version": "1.0", "event": base}


def test_customer_support_cancellation_is_refund_consumable():
    t = run_refund_triage(_ev(cancel_reason="CUSTOMER_SUPPORT"), products=PRODUCTS, app_name="Saju Today")
    assert t.event_class == "refund"
    assert t.product_kind == "consumable"
    assert "deduct" in t.entitlement_action and "5 credits" in t.entitlement_action
    assert t.reply_needed and "5 Deep Reading credits" in t.reply_draft and "Google Play" in t.reply_draft


def test_subscription_refund_revokes_now_with_base_plan_suffix():
    t = run_refund_triage(_ev(cancel_reason="CUSTOMER_SUPPORT", product_id="app_pro_monthly:p1m"), products=PRODUCTS)
    assert t.event_class == "refund" and t.product_kind == "subscription"
    assert t.entitlement_action.startswith("revoke the subscription entitlement now")


def test_unsubscribe_and_missing_reason_are_not_refunds():
    a = run_refund_triage(_ev(cancel_reason="UNSUBSCRIBE", product_id="app_pro_monthly:p1m", expiration_at_ms=1790400000000), products=PRODUCTS)
    b = run_refund_triage(_ev(product_id="app_pro_monthly:p1m"), products=PRODUCTS)
    assert a.event_class == "voluntary_cancel" and not a.reply_needed and "2026-09-26" in a.reply_draft
    assert b.event_class == "ambiguous_cancel" and b.reply_draft is None and "no change" in b.entitlement_action


def test_billing_issue_and_refund_reversed():
    b = run_refund_triage(_ev(type="BILLING_ISSUE", product_id="app_pro_monthly:p1m", grace_period_expiration_at_ms=1791000000000), products=PRODUCTS)
    r = run_refund_triage(_ev(type="REFUND_REVERSED", store="APP_STORE"), products=PRODUCTS)
    assert b.event_class == "billing_issue" and b.reply_needed and "until 2026-10-03" in b.reply_draft
    assert r.event_class == "refund_reversed" and "restore" in r.entitlement_action


def test_sandbox_anonymous_unknown_type_warn_not_raise():
    t = run_refund_triage(_ev(type="SOMETHING_NEW", environment="SANDBOX", app_user_id="$RCAnonymousID:abc"))
    assert t.event_class == "manual_review"
    joined = " ".join(t.warnings)
    assert "SANDBOX" in joined and "anonymous" in joined and "SOMETHING_NEW" in joined


def test_grant_family_and_korean_reply():
    g = run_refund_triage(_ev(type="NON_RENEWING_PURCHASE"), products=PRODUCTS)
    k = run_refund_triage(_ev(cancel_reason="CUSTOMER_SUPPORT"), products=PRODUCTS, app_name="사주클럽", language="ko")
    assert g.event_class == "grant" and not g.reply_needed
    assert "환불" in k.reply_draft and "사주클럽" in k.reply_draft


def test_examples_and_tool_wrapper():
    for name, cls in [("rc_refund_consumable", "refund"), ("rc_refund_subscription", "refund"),
                      ("rc_cancel_unsubscribe", "voluntary_cancel"), ("rc_billing_issue", "billing_issue")]:
        out = json.loads(refund_triage((EX / f"{name}.json").read_text(), products_json=json.dumps(PRODUCTS)))
        assert out["event_class"] == cls, name
