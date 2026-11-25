"""The graph's event vocabulary: what the outside world is told, and when.

    from workflows.events import stream_turn, resume_turn
    for event in stream_turn(graph, "What plan am I on?", "SUB-2041"):
        send(event.model_dump_json())

WHY AN EVENT CONTRACT
    LangGraph's raw stream is a lot of things at once: node deltas, message
    fragments, checkpoint metadata. A browser or a React page should not have to
    know any of that, and an API should not have to know which node emits what.
    So there is one small vocabulary, every member is a Pydantic model with a
    `type` field, and both the SSE endpoint and the CLI read the same objects.

    This is the seam that makes the UI possible: the graph stops talking to a
    terminal and starts talking to a contract.

THE SEVEN EVENTS
    token       a fragment of the answer, as the model writes it. From `generate`
                and from the account agent's final message ONLY — routing and
                grading stream JSON too, and their JSON must never reach a reply
                bubble.
    progress    a line a node chose to say: "looked the account up".
    tool        a tool started or finished. Emitted from the middleware, so it
                covers the agent's calls without the agent knowing.
    citation    a passage the answer relied on, as `[SLA · 3.2]`.
    interrupt   the run paused for a human. Carries the approval form.
    done        the turn finished. Carries the whole answer and the citations.
    error       the turn failed. Carries a message that is safe to show — never a
                stack trace, because a subscriber is reading it.

STREAMING IS BEST-EFFORT, THE FINAL ANSWER IS NOT
    `token` events are nice for a UI and useless for correctness: the authoritative
    answer is assembled by the graph and delivered in `done`. If the provider does
    not stream, the user sees the answer arrive at once instead of word by word —
    degraded latency, identical content.
"""
from __future__ import annotations

import json
import logging
import traceback
from typing import Any, Iterator, Literal

log = logging.getLogger("netbandhu.events")

from pydantic import BaseModel, Field


class Event(BaseModel):
    """Base: every event says what it is."""

    type: str


class Token(Event):
    type: Literal["token"] = "token"
    text: str


class Progress(Event):
    type: Literal["progress"] = "progress"
    text: str


class ToolEvent(Event):
    type: Literal["tool"] = "tool"
    name: str
    status: Literal["started", "finished", "failed"]
    detail: str = ""


class Citation(Event):
    type: Literal["citation"] = "citation"
    document: str
    section: str

    @property
    def label(self) -> str:
        return f"[{self.document} · {self.section}]"


class Interrupt(Event):
    type: Literal["interrupt"] = "interrupt"
    payload: dict[str, Any] = Field(default_factory=dict)


class Done(Event):
    type: Literal["done"] = "done"
    answer: str
    citations: list[Citation] = Field(default_factory=list)
    refused: bool = False


class Error(Event):
    type: Literal["error"] = "error"
    message: str


SAFE_ERROR = ("Something went wrong looking that up. A support engineer will follow up.")


# ── translating a run into events ────────────────────────────────────────────
def _from_update(node: str, delta: Any, seen: list[str]) -> Iterator[Event]:
    """What one node's delta becomes on the wire.

    `progress` in the state is the WHOLE list, not an increment — each node returns
    `[*state_progress, "my new line"]`, because the graph's reducer for that key is
    "last write wins". So emitting the delta naively repeats every earlier line on
    every node. `seen` remembers what has already gone out and only the tail is new;
    it is per-turn, so a new turn starts from an empty list.
    """
    if not isinstance(delta, dict):
        return
    progress = delta.get("progress") or []
    fresh = progress[len(seen):] if len(progress) >= len(seen) else progress
    seen[:] = [str(x) for x in progress]
    for line in fresh:
        # the agent's own step marker is noise to a subscriber; the CLI shows it
        if str(line).startswith("looked the account up"):
            continue
        yield Progress(text=str(line))


def _citations(values: dict) -> list[Citation]:
    out = []
    for pair in values.get("cited") or []:
        try:
            doc, section = pair
        except (TypeError, ValueError):
            continue
        out.append(Citation(document=str(doc), section=str(section)))
    return out


def stream_turn(graph, message: str, subscriber_id: str, thread_id: str,
                now: str = "") -> Iterator[Event]:
    """One conversational turn, as events. Ends with `done` or `interrupt`."""
    config = {"configurable": {"thread_id": thread_id}}
    payload = {"message": message, "subscriber_id": subscriber_id,
               "actor": "subscriber", "now": now}
    seen: list[str] = []
    try:
        for chunk in graph.stream(payload, config=config, stream_mode="updates"):
            for node, delta in chunk.items():
                if node == "__interrupt__":
                    value = delta[0].value if isinstance(delta, (list, tuple)) else delta
                    yield Interrupt(payload=value if isinstance(value, dict) else {})
                    continue
                yield from _from_update(node, delta, seen)
        yield from _finish(graph, config)
    except Exception:                       # noqa: BLE001 — the API must not leak a trace
        # The subscriber gets SAFE_ERROR; the person running the demo gets the
        # traceback on stderr. Losing the cause is how a broken Qdrant client looked
        # like "the model had a bad day" for half an hour.
        log.error("turn failed\n%s", traceback.format_exc())
        yield Error(message=SAFE_ERROR)


def resume_turn(graph, decision: dict, thread_id: str) -> Iterator[Event]:
    """Continue a paused run after a supervisor's decision."""
    from langgraph.types import Command
    config = {"configurable": {"thread_id": thread_id}}
    seen: list[str] = []
    try:
        for chunk in graph.stream(Command(resume=decision), config=config,
                                  stream_mode="updates"):
            for node, delta in chunk.items():
                if node == "__interrupt__":
                    continue
                yield from _from_update(node, delta, seen)
        yield from _finish(graph, config)
    except Exception:                       # noqa: BLE001
        log.error("resume failed\n%s", traceback.format_exc())
        yield Error(message=SAFE_ERROR)


def _finish(graph, config) -> Iterator[Event]:
    """The authoritative answer, read from the checkpoint rather than assembled."""
    snapshot = graph.get_state(config)
    values = snapshot.values or {}
    answer = (values.get("answer") or "").strip()
    if answer:
        yield Done(answer=answer, citations=_citations(values),
                   refused=bool(values.get("refused")))
    elif snapshot.next:
        # Paused with nothing to say yet: the Interrupt event already carried the form.
        return
    else:
        yield Error(message=SAFE_ERROR)


def sse(event: Event) -> str:
    """One Server-Sent Event frame.

    `type` is INCLUDED: the client switches on it, so dropping it would leave the
    browser guessing which of seven events arrived."""
    return f"data: {event.model_dump_json()}\n\n"
