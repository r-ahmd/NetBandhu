"""The desks' nodes. Each takes the state and returns only the keys it adds.

THE RULE FROM S10, KEPT
    A node that returns a key another node in the same super-step also returns is an
    InvalidUpdateError waiting for a demo. Every function here adds keys nobody else
    in the same step touches; `cited` and `progress` are read from the state and
    returned whole, by design, because exactly one node writes them per step.
"""
from __future__ import annotations

import json

from langchain.agents import create_agent

from core.config import settings
from core.exceptions import (IncidentNotFound, NotAuthorised,
                             SubscriberNotFound)
from kb import rag
from kb.store import search
from middleware import (SubscriberContext, build_middleware, foreign_account,
                        screen)
from models import build_model, stamp
from prompts.loader import load_prompts
from tools import ALL_TOOLS, READ_TOOLS, check_sla, get_subscription
from workflows.state import DeskState

ROUTE = load_prompts("route")
CREDIT = load_prompts("credit")
AGENT_SYSTEM = (
    "You are NetBandhu, the support desk for PortCity Broadband, a residential "
    "internet provider in Chattogram. You look facts up; you never recall them.\n"
    "- Use the tools for every account fact: plan, node, invoice, outage, credit.\n"
    "- Never state an amount of money that a tool did not return.\n"
    "- Never promise a credit. A billing supervisor approves every credit.\n"
    "- Reply in two or three sentences, plainly, and never ask for a subscriber id: "
    "you are told who is logged in.\n"
    "- If a lookup finds nothing, say so and say what would fix it."
)


def _history(messages: list) -> str:
    """A compact transcript for the prompts that need context but not tokens."""
    lines = []
    for m in messages[-6:]:
        role = "Subscriber" if getattr(m, "type", "") == "human" else "Desk"
        text = m.content if isinstance(m.content, str) else str(m.content)
        if text.strip():
            lines.append(f"{role}: {text.strip()[:400]}")
    return "\n".join(lines) or "(no earlier turns)"


def intake(state: DeskState) -> dict:
    """Route one turn. One structured call; four destinations, by construction."""
    from models import ask_json
    from schemas import Intent

    # Screen BEFORE any model call: an injection costs a route decision otherwise,
    # and the turn must end with a fixed sentence rather than a routed guess.
    blocked = screen(state["message"])
    if blocked:
        return {"route": "out_of_scope", "answer": blocked, "refused": True,
                "intent": {"route": "out_of_scope", "category": "abuse",
                           "reason": "blocked by the content filter"},
                "progress": ["blocked by the content filter"],
                "messages": [("human", state["message"])]}

    foreign = foreign_account(state["message"], state["subscriber_id"])
    if foreign:
        return {"route": "out_of_scope", "answer": foreign, "refused": False,
                "intent": {"route": "out_of_scope", "category": "policy",
                           "reason": "named another subscriber's account"},
                "progress": ["refused: another subscriber's account"],
                "messages": [("human", state["message"])]}

    history = _history(state.get("messages") or [])
    prompt = ROUTE["route_prompt"].format(history=history, message=state["message"])
    model = build_model()
    intent, _ = ask_json(model, Intent, [("system", prompt)], context=None)

    # A THREAD'S STATE OUTLIVES ITS TURN. Without this reset, a follow-up turn
    # inherits the previous turn's `answer` and `verdict`, and `route()` sends it
    # to `respond` with yesterday's reply — which is exactly what happened in the
    # first end-to-end run: "can I have the credit again?" got the old approval
    # sentence instead of asking the ledger. Every one of these keys is rebuilt
    # by this turn's nodes, so clearing them is safe; `messages` and
    # `subscriber_id` are NOT cleared, because they are the thread.
    updates = {
        # THE BUG THIS FIXES. `history` is built from `messages`, but nothing ever
        # APPENDED to `messages` on the normal path — only on the two refusal paths
        # did a message get recorded. So every turn saw "(no earlier turns)" and a
        # follow-up like "and what node is that?" was routed as out_of_scope. The
        # thread id was correct, the checkpointer was correct, and the conversation
        # was empty: a memory that stores nothing is not a memory.
        "messages": [("human", state["message"])],
        "intent": intent.model_dump(),
        "route": intent.route,
        "history": history,
        "question": state["message"],
        "query": state["message"],
        "attempts": 1,
        "answer": "",
        "refused": False,
        "cited": [],
        "progress": [],
        "documents": [],
        "relevant": None,
        "grade_reason": "",
        "verdict": {},
        "proposal": {},
        "decision": {},
        "outcome": {},
    }
    if intent.needs_clarification:
        # A route we cannot take yet is a question, not a guess.
        updates["answer"] = intent.clarification_question or (
            "Could you tell me which connection this is about?")
        updates["refused"] = False
    return updates


def route(state: DeskState) -> str:
    if state.get("answer"):
        return "respond"
    return state.get("route", "out_of_scope")


# ── E3: memory of the person, across threads ─────────────────────────────────
def recall(state: DeskState) -> dict:
    """Read the subscriber's saved preferences and fold them into the history.

    A failure here must never cost an answer: the Store is an enhancement, so an
    unavailable store degrades to "no memories", not to a failed turn.
    """
    try:
        from memory import open_memory, recall as read_memories
        with open_memory() as store:
            notes = read_memories(store, state["subscriber_id"], limit=5)
    except Exception:
        notes = []
    if not notes:
        return {"memories": []}
    extra = "\nWhat the desk remembers about this subscriber:\n" + "\n".join(
        f"- {n}" for n in notes)
    return {"memories": notes,
            "history": (state.get("history") or "") + extra}


def remember_turn(state: DeskState) -> dict:
    """The one terminal node: close the thread's turn, then maybe save a preference.

    EVERY path ends here, which is why the reply is appended here and not in each
    of the six answer-writing nodes: one writer, one message, no path that forgets.
    """
    answer = state.get("answer") or ""
    done = {"messages": [("ai", answer)]} if answer else {}
    if not answer or len(answer) < 20:
        return done
    try:
        from memory import extract, open_memory, remember
        note = extract(state["message"], answer, state["subscriber_id"])
        if note.text.strip():
            with open_memory() as store:
                remember(store, state["subscriber_id"], note.text.strip())
            done["progress"] = [*state.get("progress", []), "saved a preference"]
    except Exception:
        pass          # memory is an enhancement; it must not break a turn
    return done


# ── account: the agent with the read tools (C3) ──────────────────────────────
def account(state: DeskState) -> dict:
    """Answer an account question through the tool loop, then hand over to compose.

    The agent's own narration is NOT the answer: `compose` rewrites it against the
    citation contract, so a tool result can never be pasted to a subscriber as-is.
    """
    agent = create_agent(
        build_model(), READ_TOOLS,
        system_prompt=AGENT_SYSTEM,
        middleware=build_middleware(),
        context_schema=SubscriberContext,
    )
    ctx = SubscriberContext(state["subscriber_id"])
    out = agent.invoke({"messages": state.get("messages") or [("human", state["message"])]},
                       context=ctx)
    facts = out["messages"][-1].content
    facts = facts if isinstance(facts, str) else str(facts)
    return {"answer": facts, "evidence": facts,
            "progress": [*state.get("progress", []), "looked the account up"]}


# ── policy: corrective RAG (C6 + E1) ────────────────────────────────────────
def policy_retrieve(state: DeskState) -> dict:
    docs = search(state.get("query") or state["question"], k=settings.RAG_K)
    return {"documents": docs,
            "progress": [*state.get("progress", []),
                         f"searched the policies ({len(docs)} passages)"]}


def policy_grade(state: DeskState) -> dict:
    verdict = rag.grade(state)
    return verdict


def policy_rewrite(state: DeskState) -> dict:
    return rag.rewrite(state)


def policy_after_grade(state: DeskState) -> str:
    return rag.after_grade(state)


def policy_generate(state: DeskState) -> dict:
    out = rag.generate({**state, "history": state.get("history", "")})
    return {"answer": out["answer"], "cited": [list(c) for c in out.get("cited", [])],
            "refused": out.get("refused", False)}


def respond(state: DeskState) -> dict:
    """The terminal node for the clarification path: the answer, or the refusal."""
    if state.get("answer"):
        return {"answer": state["answer"]}
    return {"answer": rag.REFUSAL, "refused": True}


def out_of_scope(state: DeskState) -> dict:
    return {"answer": rag.REFUSAL, "refused": True,
            "progress": [*state.get("progress", []), "not a support question"]}


# ── credit: assess with the tool, then the subgraph decides (C5 + E2) ───────
def credit_assess(state: DeskState) -> dict:
    """Find the incident and compute the verdict — with the tool, not the model.

    A credit request that names no incident is resolved from the live outage on the
    subscriber's own node: that is what a subscriber means by "the days it was down".
    """
    sid = state["subscriber_id"]
    sub = get_subscription.invoke({"subscriber_id_or_phone": sid})
    inc_id = (state.get("intent") or {}).get("incident_id") or None
    if not inc_id:
        from tools import backend
        live = [iid for iid, inc in backend()["outages"].items()
                if inc["node"] == sub["node"] and sid in inc["affected"]]
        if not live:
            return {"answer": ("There is no outage on your node in our records, so there "
                               "is nothing to credit. If your connection is still down, "
                               "tell me and I will have an engineer check the line."),
                    "refused": False}
        inc_id = sorted(live)[-1]

    try:
        verdict = check_sla.invoke({"incident_id": inc_id, "subscriber_id": sid,
                                    "now": state.get("now", "")})
    except NotAuthorised as exc:
        return {"answer": ("That incident is not on your connection, so I can't assess a "
                           "credit for it. I can only see the account you're signed in "
                           "with."), "refused": False,
                "progress": [*state.get("progress", []), "refused another account's incident"]}
    except (IncidentNotFound, SubscriberNotFound) as exc:
        return {"answer": ("I couldn't find that incident on your account. Tell me the "
                           "date it went down and I'll look again."),
                "refused": False,
                "progress": [*state.get("progress", []), f"lookup failed: {type(exc).__name__}"]}
    updates = {"verdict": verdict, "progress": [*state.get("progress", []),
                                                verdict["sentence"]]}
    return updates


def credit_branch(state: DeskState) -> str:
    verdict = state.get("verdict") or {}
    if verdict.get("qualifies") and not verdict.get("already_credited"):
        return "credit_flow"
    return "credit_explain"


def credit_explain(state: DeskState) -> dict:
    """No credit due, or already credited: explain the verdict and cite the clause."""
    verdict = state["verdict"]
    clause = ("[SLA · 3.5]" if verdict.get("already_credited") else
              "[SLA · 3.1]" if not verdict.get("qualifies") else "[SLA · 3.2]")
    return {"answer": f"{verdict['sentence']} {clause}",
            "cited": [["SLA", clause.strip("[]").split(" · ")[1]]],
            "refused": False}


def credit_compose(state: DeskState) -> dict:
    """Turn the outcome into a sentence for the subscriber.

    Approval and rejection both end here. A rejection carries the supervisor's reason
    because the subscriber is told it — that is the product decision in §6 of the
    proposal, and this is where it is kept.
    """
    outcome, proposal = state.get("outcome") or {}, state.get("proposal") or {}
    status = outcome.get("status")
    if status == "rejected":
        answer = (f"Our billing supervisor did not approve the credit for "
                  f"{proposal.get('incident')}. Their reason: {outcome.get('reason')}. "
                  f"You can raise it again with the support line. [SLA · 3.4]")
    elif status == "already_credited":
        answer = (f"{proposal.get('incident')} has already been credited, so nothing "
                  f"further is due for it. [SLA · 3.5]")
    else:
        answer = (f"Approved. A credit of ৳{outcome.get('amount', 0):,.0f} has been "
                  f"applied to your account ({outcome.get('id')}) for "
                  f"{proposal.get('incident')}, under SLA §3.2. It will appear on your "
                  f"next invoice. [SLA · 3.3]")
    return {"answer": answer,
            "cited": [["SLA", "3.2"]], "refused": False}


__all__ = [
    "intake", "route", "account", "recall", "remember_turn", "policy_retrieve", "policy_grade", "policy_rewrite",
    "policy_after_grade", "policy_generate", "respond", "out_of_scope",
    "credit_assess", "credit_branch", "credit_explain", "credit_compose",
]
