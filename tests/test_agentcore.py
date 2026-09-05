"""AgentCore Runtime contract on the same app: /ping, /invocations raw dispatch, error mapping."""
from pathlib import Path

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from release_keeper.agentcore import app

EX = Path(__file__).resolve().parent.parent / "examples"
c = TestClient(app)


def test_ping_contract():
    r = c.get("/ping")
    assert r.status_code == 200 and r.json() == {"status": "Healthy"}


def test_invocations_raw_check():
    r = c.post("/invocations", json={"tool": "check", "mode": "raw", "payments": True, "category": "fortune",
                                     "text": (EX / "saju_listing_bad.txt").read_text(encoding="utf-8")})
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "success" and j["tool"] == "check" and j["response"]["summary"]["verdict"] == "BLOCK"


def test_invocations_raw_refund_and_notes():
    r = c.post("/invocations", json={"tool": "refund", "event": (EX / "rc_refund_consumable.json").read_text(encoding="utf-8"),
                                     "products": (EX / "products.json").read_text(encoding="utf-8")})
    assert r.status_code == 200 and r.json()["response"]["event_class"]
    r = c.post("/invocations", json={"tool": "notes", "version": "1.2.0",
                                     "git_log": (EX / "gitlog_sample.txt").read_text(encoding="utf-8")})
    assert r.status_code == 200 and r.json()["response"]["store_whats_new_chars"] <= 500


def test_invocations_errors(monkeypatch):
    assert c.post("/invocations", json={"tool": "nope"}).status_code == 400
    assert c.post("/invocations", json={}).status_code == 400
    assert c.post("/invocations", json={"tool": "check"}).status_code == 400          # missing required field
    assert c.post("/invocations", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    for k in ("RK_API_KEY", "OPENROUTER_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    assert c.post("/invocations", json={"prompt": "hello"}).status_code == 503          # agent mode without a key


def test_web_routes_still_served():
    assert c.get("/health").json()["ok"] is True
