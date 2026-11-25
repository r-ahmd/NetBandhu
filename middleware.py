"""The rails the agent runs inside: PII, scope, authorisation, limits, retry.

    from middleware import build_middleware, SubscriberContext
    agent = create_agent(model, tools, middleware=build_middleware())

MIDDLEWARE IS CODE AROUND THE LOOP
    Each entry runs before the agent starts, before or after each model call, or
    wrapped around each tool call. The agent itself does not change. Each rail does
    ONE job, and the order below is the order they run:

    1. content_filter    ends the run politely on a known injection or abuse phrase
    2. PII               phone numbers and emails never reach the model or a reply
    3. authorize         a subscriber only ever touches their OWN record
    4. limits            a fuse: too many model or tool calls ends the run
    5. retry + safe error a tool that RAISES a transient error is retried, then
                         the model is told it could not check

WHY AUTHORISATION IS NOT A PROMPT RULE
    "Only discuss the logged-in subscriber's account" in a system prompt is a wish:
    anything in the conversation can argue with it. `authorize` compares the record
    the tool is about to touch with the subscriber id supplied by the CALLER as
    runtime context, and returns an error the model cannot talk its way past. The
    prompt says it too, because a model that knows the rule wastes fewer calls.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from langchain.agents.middleware import (
    AgentMiddleware,
    before_model,
    ModelCallLimitMiddleware,
    PIIMiddleware,
    ToolCallLimitMiddleware,
    ToolRetryMiddleware,
    wrap_tool_call,
)
from langchain_core.messages import ToolMessage

from core.config import settings
from tools import backend


OTHER_ACCOUNT_RE = re.compile(r"\b(SUB-\d{4})\b", re.I)


@dataclass
class SubscriberContext:
    """Who is logged in. Supplied by the caller on every invoke, never by the model.

    In production this comes from the session cookie; in the CLI, from `--as`.
    """
    subscriber_id: str
    name: str = ""



# ── 2. PII ───────────────────────────────────────────────────────────────────
# The built-in detectors cover email, card, IP, MAC and URL. A Bangladeshi mobile
# number is not among them, and it is the one identifier this desk sees most, so
# the detector is a regex we own. `strategy="mask"` rather than "redact": the last
# four digits are how the call centre verifies a caller, so they must survive.
_PHONE_RE = re.compile(r"(?:\+?880|0)1[3-9]\d{8}")


def _phone_detector(text: str) -> list:
    """Find local mobile numbers. Returns the middleware's PIIMatch objects."""
    from langchain.agents.middleware._redaction import PIIMatch
    return [PIIMatch(type="phone", value=m.group(0), start=m.start(), end=m.end())
            for m in _PHONE_RE.finditer(text or "")]


# ── 1. the content filter ────────────────────────────────────────────────────
INJECTION_PATTERNS = [
    re.compile(r"ignore (all )?(your |the )?(previous |prior )?instructions", re.I),
    re.compile(r"you are now (a|an) ", re.I),
    re.compile(r"system prompt", re.I),
    re.compile(r"disregard (the )?(above|rules)", re.I),
    re.compile(r"reveal (your|the) (prompt|instructions)", re.I),
]
ABUSE_PATTERNS = [
    re.compile(r"\b(kill|murder|hurt) (you|yourself)\b", re.I),
]

BLOCKED_REPLY = ("I can't help with that. If you'd like help with your connection or "
                 "your bill, tell me what you're seeing and I'll look it up.")


def foreign_account(text: str, me: str) -> str | None:
    """A message naming a subscriber id that is not the caller's is refused here.

    The authorisation rail would also catch it, at the tool call — but by then the
    model has already been asked to help with someone else's account, and the
    honest answer ("I can only see your account") is a product decision, not a
    tool error. Refusing at intake also keeps the refusal deterministic, which is
    what makes it testable.
    """
    for found in OTHER_ACCOUNT_RE.findall(text or ""):
        if found.upper() != (me or "").upper():
            return ("I can only see the account you're signed in with, so I can't look "
                    "up another subscriber's connection. They can sign in with their own "
                    "number, or call the support line with their subscriber ID.")
    return None


def screen(text: str) -> str | None:
    """None when the text is fine, or the fixed reply when it is not.

    WHY THIS IS A FUNCTION AND NOT ONLY A MIDDLEWARE HOOK
        It started as `@wrap_tool_call` only, which fires when the AGENT calls a
        tool. The credit desk calls `check_sla` directly from a node, so no tool
        call happens through the agent there — and the first adversarial run walked
        straight past the filter into the credit branch. A rail that covers one of
        two paths is not a rail. `intake` calls this for every turn, and the
        middleware keeps it for tool results.
    """
    for pat in INJECTION_PATTERNS + ABUSE_PATTERNS:
        if pat.search(text or ""):
            return BLOCKED_REPLY
    return None


@wrap_tool_call
def content_filter(request, handler):
    """The same screen, applied to whatever the agent is about to do.

    This is a cheap net, not the defence: the real defence is structural, because
    the only path to money is the approval node. It exists so an obvious attack
    ends the turn instead of burning a budget discovering it.
    """
    text = ""
    for msg in (request.state.get("messages") or []):
        if getattr(msg, "type", "") == "human":
            text = msg.content if isinstance(msg.content, str) else str(msg.content)
    blocked = screen(text)
    if blocked:
        return ToolMessage(
            content=json.dumps({"error": "blocked: this request was not processed"}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error")
    return handler(request)


# ── 3. authorisation ─────────────────────────────────────────────────────────
def _record_owner(tool_name: str, args: dict) -> str | None:
    """Which subscriber does this call touch? None means 'not a personal record'."""
    if tool_name in ("get_subscription", "get_invoice", "check_sla", "propose_credit"):
        sid = (args.get("subscriber_id") or args.get("subscriber_id_or_phone") or "").strip()
        if sid in backend()["subscribers"]:
            return sid
        for known, rec in backend()["subscribers"].items():
            if rec.get("phone") == sid:
                return known
        return None
    return None


@wrap_tool_call
def authorize(request, handler):
    """Run the tool only if the record belongs to the logged-in subscriber.

    The model may well ASK for someone else's order — it is trying to help whoever
    is typing. Whether it may is not the model's decision, so it is code.
    """
    me: SubscriberContext | None = request.runtime.context if request.runtime else None
    owner = _record_owner(request.tool_call["name"], request.tool_call.get("args") or {})
    if owner is not None and (me is None or owner != me.subscriber_id):
        return ToolMessage(
            content=json.dumps({
                "error": "not authorised: that record belongs to another subscriber. "
                         "Do not reveal anything about it, and do not try again."}),
            tool_call_id=request.tool_call["id"],
            name=request.tool_call["name"],
            status="error")
    return handler(request)


# ── 5. retry, then a safe error ──────────────────────────────────────────────
TRANSIENT = (TimeoutError, ConnectionError)


def tool_unavailable(exc: Exception, request) -> str | None:
    """What the model is told when a tool is still failing after its retries.

    A raw exception is not an answer: it either leaks a stack trace into a
    subscriber's chat or tempts the model to invent what the tool would have said.
    """
    return json.dumps({"error": "that lookup is unavailable right now. "
                                "Tell the subscriber you could not check and that "
                                "a support engineer will follow up."})


RETRY = ToolRetryMiddleware(max_retries=2, retry_on=TRANSIENT, initial_delay=0.5,
                            on_failure="error")


@before_model
def inject_caller(state, runtime):
    """Tell the model WHO is logged in, from runtime context rather than the chat.

    Without this the agent asks a subscriber for their own subscriber id — which is
    a bad experience and, worse, invites the model to accept an id typed into the
    conversation. The id here comes from the session (the CLI's `--as`), so a
    subscriber cannot change it by typing a different one.
    """
    me: SubscriberContext | None = getattr(runtime, "context", None) if runtime else None
    if me is None or not me.subscriber_id:
        return None
    note = (f"The logged-in subscriber is {me.subscriber_id}"
            + (f" ({me.name})" if me.name else "")
            + ". Their own id is the one to pass to tools; never ask for it and never "
              "use an id that appears in the conversation if it differs.")
    from langchain_core.messages import SystemMessage
    return {"messages": [SystemMessage(content=note)]}


def build_middleware() -> list[Any]:
    """The rails, in the order they run. Read the module docstring for why."""
    return [
        content_filter,
        inject_caller,
        PIIMiddleware("email", strategy="redact", apply_to_input=True,
                      apply_to_tool_results=True),
        PIIMiddleware("phone", strategy="mask", apply_to_input=True,
                      apply_to_tool_results=True, detector=_phone_detector),
        authorize,
        ModelCallLimitMiddleware(run_limit=settings.MAX_MODEL_CALLS_PER_RUN,
                                 exit_behavior="end"),
        ToolCallLimitMiddleware(run_limit=settings.MAX_TOOL_CALLS_PER_RUN,
                                exit_behavior="end"),
        RETRY,
    ]


if __name__ == "__main__":
    from models import build_model
    from tools import READ_TOOLS
    from langchain.agents import create_agent

    agent = create_agent(
        build_model(), READ_TOOLS,
        system_prompt="You are NetBandhu, a broadband support assistant. Use the tools.",
        middleware=build_middleware(),
        context_schema=SubscriberContext,
    )
    ctx = SubscriberContext("SUB-2041", "Shilpa Akter")
    out = agent.invoke({"messages": [("human", "What plan am I on?")]},
                       context=ctx)
    print(out["messages"][-1].content[:300])
