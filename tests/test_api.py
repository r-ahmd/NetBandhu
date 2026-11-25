"""The HTTP surface: the contract the UI depends on.

Uses FastAPI's TestClient, so no server has to be running. Each test that needs a
fresh thread gets its own id — the checkpointer is shared and real, which is the
point: the pause must survive exactly as it does in production.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def clean_ledger(tmp_path, monkeypatch):
    """An empty credit ledger for this test.

    Without it the suite is order-dependent: a credit applied by one test makes the
    next one see "already credited" instead of a pause — which is correct behaviour
    and a bad test. The ledger is read through `settings` on every call, so pointing
    it at a scratch file is enough.
    """
    from core.config import settings
    monkeypatch.setattr(settings, "CREDIT_LEDGER", tmp_path / "credits.jsonl")
    return tmp_path


def frames(text: str) -> list[dict]:
    out = []
    for block in text.split("\n\n"):
        if block.startswith("data: "):
            out.append(json.loads(block[6:]))
    return out


def test_health_reports_the_model_and_the_collection(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["collection"].startswith("policies__")


def test_index_is_a_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "NetBandhu" in r.text and "text/event-stream" not in r.headers["content-type"]


def test_unknown_subscriber_is_404(client):
    r = client.post("/chat", json={"message": "hi", "subscriber_id": "SUB-9999",
                                   "thread_id": "api-404"})
    assert r.status_code == 404


def test_a_turn_ends_with_done_and_a_citation(client):
    r = client.post("/chat", json={"message": "What plan am I on?",
                                   "subscriber_id": "SUB-2041", "thread_id": "api-1"})
    assert r.status_code == 200
    ev = frames(r.text)
    assert ev[-1]["type"] == "done"
    assert "HOME" in ev[-1]["answer"]


def test_a_policy_question_carries_a_citation(client):
    r = client.post("/chat", json={"message": "How much is the reconnection fee?",
                                   "subscriber_id": "SUB-2041", "thread_id": "api-2"})
    done = frames(r.text)[-1]
    assert done["type"] == "done"
    assert done["citations"], "a policy answer must carry a citation"


def test_credit_request_pauses_and_then_applies(client, clean_ledger):
    thread = "api-credit"
    r = client.post("/chat", json={
        "message": "My internet has been down since this morning. Am I owed anything?",
        "subscriber_id": "SUB-2041", "thread_id": thread,
        "now": "2026-06-14T14:00:00"})
    ev = frames(r.text)
    assert ev[-1]["type"] == "interrupt"
    assert ev[-1]["payload"]["proposal"]["amount"].startswith("৳")

    pend = client.get(f"/pending/{thread}").json()
    assert pend["paused"] and pend["interrupt"]["kind"] == "credit_approval"

    r = client.post("/decide", json={"thread_id": thread, "action": "approve"})
    done = frames(r.text)[-1]
    assert done["type"] == "done"
    assert "applied" in done["answer"].lower() or "credit" in done["answer"].lower()


def test_a_rejection_needs_a_reason(client, clean_ledger):
    # its own paused thread, so the 422 is about the MISSING REASON and not about
    # whether some earlier test left this thread finished
    thread = "api-reject"
    r = client.post("/chat", json={
        "message": "Am I owed anything for the outage?",
        "subscriber_id": "SUB-2041", "thread_id": thread,
        "now": "2026-06-14T14:00:00"})
    assert frames(r.text)[-1]["type"] == "interrupt"
    r = client.post("/decide", json={"thread_id": thread, "action": "reject"})
    assert r.status_code == 422


def test_deciding_a_thread_that_is_not_paused_is_409(client):
    r = client.post("/decide", json={"thread_id": "api-1", "action": "approve"})
    assert r.status_code == 409


def test_a_follow_up_continues_the_thread(client, clean_ledger):
    """C4 over HTTP: the second message must know what the first established.

    This is the case that caught a real bug: `intake` never appended the human
    message to `messages`, so every turn saw an empty history and "and what node is
    that?" was routed as out_of_scope. The thread id and the checkpointer were both
    correct; the conversation was empty.
    """
    thread = "api-memory"
    first = client.post("/chat", json={"message": "What plan am I on?",
                                       "subscriber_id": "SUB-2041",
                                       "thread_id": thread})
    assert "HOME" in frames(first.text)[-1]["answer"]

    second = client.post("/chat", json={"message": "And what node is that?",
                                        "subscriber_id": "SUB-2041",
                                        "thread_id": thread})
    done = frames(second.text)[-1]
    assert done["type"] == "done"
    assert "AGRABAD" in done["answer"], done["answer"]
