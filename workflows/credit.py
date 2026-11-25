"""The money path: assess → approval → apply. A subgraph, and the only way money moves.

    START → assess ─(qualifies)→ approval[interrupt] → apply → END
                   └(nothing due)→ END

WHY THE PAUSE AND THE PAYMENT ARE DIFFERENT NODES
    The manager's decision must be checkpointed before any money moves. Two nodes
    means the approval's output is written to the checkpoint, and only then does
    `apply` run — so a crash between them resumes at `apply` with the decision
    already durable, instead of asking the supervisor again or skipping the payment.

COMPILED WITHOUT A CHECKPOINTER, ON PURPOSE
    The contract that carries the pause must, by design, be the one inheriting the
    parent graph's, and that is exactly what `compile()` with no checkpointer does
    when the subgraph is invoked inside a node of a compiled-parent graph. Give
    this graph its own saver and the paused state lands somewhere the parent's
    thread id cannot find, and `Command(resume=...)` silently starts over.

IDEMPOTENCY IS THE LEDGER, NOT A HOPE
    `apply_credit` refuses a second credit for the same (subscriber, incident), so a
    resume after a crash cannot pay twice. tools.py owns that rule; this file only
    calls it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # `python workflows/x.py`

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from core.exceptions import AlreadyCredited
from schemas import ManagerDecision
from tools import apply_credit, backend
from workflows.state import DeskState


def assess(state: DeskState) -> dict:
    """Build the proposal a human will decide on. Writes nothing."""
    verdict = state.get("verdict") or {}
    if not verdict.get("qualifies") or verdict.get("already_credited"):
        return {"progress": [*state.get("progress", []),
                             "nothing to approve: no credit is due"]}

    sid = state["subscriber_id"]
    rec = backend()["subscribers"][sid]
    incident = verdict["incident"]
    inc = backend()["outages"][incident]

    proposal = {
        "subscriber_id": sid,
        "subscriber_name": rec["name"],
        "incident": incident,
        "node": inc["node"],
        "downtime_hours": verdict["downtime_hours"],
        "clause": verdict["clause"],
        "amount": float(verdict["amount"]),
        "reason": (f"{incident} down {verdict['downtime_hours']:.2f} h on "
                   f"{inc['node']} ({inc['cause']}); exceeds the 4-hour threshold "
                   f"in SLA §3.1."),
    }
    return {"proposal": proposal,
            "progress": [*state.get("progress", []),
                         f"proposed ৳{proposal['amount']:,.0f} for {incident}, "
                         f"awaiting the billing supervisor"]}


def needs_manager(state: DeskState) -> str:
    """A proposal waits for a person; nothing due has nothing to decide."""
    return "approval" if state.get("proposal") else "__end__"


def approval(state: DeskState) -> dict:
    """Pause with the proposal. RULE: on resume this node runs again from line 1.

    So nothing here may have a side effect, and the progress line is written before
    the interrupt rather than after it — otherwise the subscriber sees it twice.
    """
    proposal = state["proposal"]
    payload = {
        "kind": "credit_approval",
        "proposal": {
            "subscriber": f"{proposal['subscriber_name']} ({proposal['subscriber_id']})",
            "incident": proposal["incident"],
            "node": proposal["node"],
            "downtime": f"{proposal['downtime_hours']:.2f} h",
            "clause": proposal["clause"],
            "amount": f"৳{proposal['amount']:,.0f}",
            "reason": proposal["reason"],
        },
        "your_options": ["approve", "edit <amount>", "reject <reason>"],
    }
    decision = interrupt(payload)
    parsed = ManagerDecision.model_validate(
        decision if isinstance(decision, dict) else {"action": "reject",
                                                     "reason": str(decision)})
    return {"decision": parsed.model_dump()}


def pay(state: DeskState) -> dict:
    """Apply the credit if approved, or record the rejection. Never raises to the user."""
    proposal, decision = state["proposal"], state["decision"]
    if decision["action"] == "reject":
        return {"outcome": {"status": "rejected", "reason": decision.get("reason")},
                "progress": [*state.get("progress", []), "the supervisor rejected it"]}

    amount = proposal["amount"] if decision["action"] == "approve" else decision["amount"]
    try:
        record = apply_credit(proposal["subscriber_id"], proposal["incident"],
                              amount, proposal["clause"], approved_by="billing supervisor")
        return {"outcome": {"status": "applied", **record},
                "progress": [*state.get("progress", []),
                             f"applied ৳{record['amount']:,.0f} as {record['id']}"]}
    except AlreadyCredited as exc:
        return {"outcome": {"status": "already_credited", "detail": str(exc)},
                "progress": [*state.get("progress", []), "already credited; not paid again"]}


def build_credit_flow():
    b = StateGraph(DeskState)
    b.add_node("assess", assess)
    b.add_node("approval", approval)
    b.add_node("pay", pay)
    b.add_edge(START, "assess")
    b.add_conditional_edges("assess", needs_manager, ["approval", END])
    b.add_edge("approval", "pay")
    b.add_edge("pay", END)
    return b.compile()


if __name__ == "__main__":
    g = build_credit_flow()
    print("topology: assess -[proposal]-> approval[interrupt] -> pay")
    print("nodes:", list(g.get_graph().nodes))
