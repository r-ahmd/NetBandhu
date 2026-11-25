"""The HTTP surface: a chat endpoint, the pause, and the approval.

    uv run uvicorn api:app --reload --port 8077
    open http://127.0.0.1:8077

WHY THIS FILE IS SMALL
    Everything it needs already exists. The graph does the work, `workflows/events.py`
    owns the vocabulary, and this layer only translates HTTP into calls on those two.
    A "serve it" session that rewrites the assistant in a web framework has the
    dependency backwards: the assistant is the product, the HTTP is a delivery
    mechanism.

WHY SSE AND NOT WEBSOCKETS
    A chat reply is one-directional and finite: tokens flow out, nothing flows back
    until the turn ends. Server-Sent Events do that over ordinary HTTP with no
    protocol upgrade, no reconnect logic of our own, and they survive a proxy that
    was configured by someone who has never heard of this project.

THE TWO ENDPOINTS THAT MATTER
    POST /chat      runs one turn and streams events until `done` OR `interrupt`
    POST /decide    resumes a paused run with a supervisor's decision

    `interrupt` is not an error and not a finish: the client shows the approval form
    and calls /decide. That is the whole of human-in-the-loop over HTTP.
"""
from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent))

from core.config import settings
from tools import backend
from workflows.events import Error, resume_turn, sse, stream_turn
from workflows.graph import build

STATIC = Path(__file__).resolve().parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """One checkpointer for the process, opened once.

    SQLite over `check_same_thread=False` because FastAPI runs sync endpoints on a
    worker thread. The connection is closed on shutdown, which is what lets the next
    `uvicorn` start read the same file without waiting on the embedded lock.
    """
    import sqlite3
    from langgraph.checkpoint.sqlite import SqliteSaver

    settings.ensure_dirs()
    conn = sqlite3.connect(str(settings.CHECKPOINT_DB), check_same_thread=False)
    app.state.conn = conn
    app.state.graph = build(checkpointer=SqliteSaver(conn))
    try:
        yield
    finally:
        conn.close()


app = FastAPI(title="NetBandhu", version=settings.VERSION, lifespan=lifespan)


# ── the contract ─────────────────────────────────────────────────────────────
class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    subscriber_id: str = Field(default="SUB-2041")
    thread_id: str = Field(default="web-1")
    now: str = Field(default="",
                     description="ISO clock for a LIVE incident; empty uses the ETA. "
                                 "A demo needs a fixed clock, not the wall clock.")


class DecideIn(BaseModel):
    thread_id: str
    action: Literal["approve", "edit", "reject"]
    amount: float | None = None
    reason: str | None = None


def _frames(events):
    """Turn an event iterator into SSE bytes, and never let one event kill the stream."""
    for event in events:
        yield sse(event)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = STATIC / "index.html"
    if not page.exists():
        return "<h1>NetBandhu</h1><p>static/index.html is missing.</p>"
    return page.read_text(encoding="utf-8")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": settings.model_id, "fallback": settings.FALLBACK,
            "collection": settings.collection, "version": settings.VERSION}


@app.get("/subscribers")
def subscribers() -> dict:
    """The demo logins. A real deployment reads the session, not this list."""
    return {sid: {"name": rec["name"], "node": rec["node"], "plan": rec["plan"]}
            for sid, rec in backend()["subscribers"].items()}


@app.post("/chat")
def chat(body: ChatIn) -> StreamingResponse:
    graph = app.state.graph
    if body.subscriber_id not in backend()["subscribers"]:
        raise HTTPException(status_code=404, detail="unknown subscriber")
    events = stream_turn(graph, body.message, body.subscriber_id,
                         body.thread_id, body.now)
    return StreamingResponse(_frames(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/pending/{thread_id}")
def pending(thread_id: str) -> dict:
    """Is this thread paused, and with what? The UI asks on page load."""
    snapshot = app.state.graph.get_state({"configurable": {"thread_id": thread_id}})
    interrupts = list(snapshot.interrupts or ())
    return {"paused": bool(snapshot.next),
            "interrupt": interrupts[0].value if interrupts else None}


@app.post("/decide")
def decide(body: DecideIn) -> StreamingResponse:
    """Resume a paused run. The supervisor's decision is the body."""
    graph = app.state.graph
    snapshot = graph.get_state({"configurable": {"thread_id": body.thread_id}})
    if not snapshot.next:
        raise HTTPException(status_code=409,
                            detail="this thread is not paused; there is nothing to decide")

    decision = {"action": body.action}
    if body.action == "edit":
        if not body.amount or body.amount <= 0:
            raise HTTPException(status_code=422, detail="an edit needs a positive amount")
        decision["amount"] = body.amount
    if body.action == "reject":
        if not (body.reason or "").strip():
            raise HTTPException(status_code=422,
                                detail="a rejection needs a reason: the subscriber is told it")
        decision["reason"] = body.reason.strip()

    return StreamingResponse(_frames(resume_turn(graph, decision, body.thread_id)),
                             media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})
