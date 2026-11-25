"""The NetBandhu graph: one router, four destinations, one human pause.

    START → intake ─┬─ policy_qa   retrieve → grade ─(poor)→ rewrite ─┐
                    │                     └─(good)───────────────────→ generate → END
                    ├─ account     the tool-loop agent → END
                    ├─ credit_request  assess → [credit_flow: approval[interrupt] → pay] → compose
                    ├─ out_of_scope → refusal → END
                    └─(clarify)→ respond → END

WHY A GRAPH AND NOT AN AGENT WITH ALL THE TOOLS
    An agent chooses its own next step, which is right for "look this up". It cannot
    guarantee that money passes a human, that a policy answer is graded before it is
    written, or that a failed search is retried twice and no more. Those are
    guarantees about ORDER, and order is an edge. So the edges carry the guarantees
    and the agent is used only where judgement is genuinely needed: the account desk.

THE PAUSE SURVIVES A RESTART BECAUSE OF THE CHECKPOINTER
    `build(checkpointer=...)` writes state after every super-step. The credit
    subgraph is compiled WITHOUT one, so it inherits this graph's — which is what
    lets interrupt() inside it pause the whole run and Command(resume=...) on the
    same thread continue it (S11, §7).
"""
from __future__ import annotations

import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph

from core.config import settings
from workflows import nodes
from workflows.credit import build_credit_flow
from workflows.state import DeskState


def build(checkpointer=None):
    """Compile the graph. Without a checkpointer it cannot pause — and says so."""
    b = StateGraph(DeskState)
    b.add_node("intake", nodes.intake)
    b.add_node("recall", nodes.recall)
    b.add_node("remember", nodes.remember_turn)
    b.add_node("account", nodes.account)
    b.add_node("retrieve", nodes.policy_retrieve)
    b.add_node("grade", nodes.policy_grade)
    b.add_node("rewrite", nodes.policy_rewrite)
    b.add_node("generate", nodes.policy_generate)
    b.add_node("respond", nodes.respond)
    b.add_node("out_of_scope", nodes.out_of_scope)
    b.add_node("credit_assess", nodes.credit_assess)
    b.add_node("credit_explain", nodes.credit_explain)
    b.add_node("credit_compose", nodes.credit_compose)
    b.add_node("credit_flow", build_credit_flow())

    b.add_edge(START, "intake")
    b.add_edge("intake", "recall")
    b.add_conditional_edges("recall", nodes.route, {
        "policy_qa": "retrieve",
        "account": "account",
        "credit_request": "credit_assess",
        "out_of_scope": "out_of_scope",
        "respond": "respond",
    })
    b.add_edge("retrieve", "grade")
    b.add_conditional_edges("grade", nodes.policy_after_grade,
                            ["generate", "rewrite"])
    b.add_edge("rewrite", "retrieve")
    b.add_edge("generate", "remember")
    b.add_edge("account", "remember")
    b.add_edge("respond", "remember")
    b.add_edge("out_of_scope", "remember")
    b.add_edge("remember", END)

    b.add_conditional_edges("credit_assess", nodes.credit_branch,
                            ["credit_flow", "credit_explain"])
    b.add_edge("credit_flow", "credit_compose")
    b.add_edge("credit_explain", "remember")
    b.add_edge("credit_compose", "remember")

    if checkpointer is None:
        print("(compiled without a checkpointer: this graph cannot pause for approval)")
    return b.compile(checkpointer=checkpointer)


@contextmanager
def open_graph():
    """The real thing: SQLite checkpoints on disk, so a pause outlives the process."""
    settings.ensure_dirs()
    conn = sqlite3.connect(str(settings.CHECKPOINT_DB), check_same_thread=False)
    try:
        yield build(checkpointer=SqliteSaver(conn))
    finally:
        conn.close()


def mermaid() -> str:
    """The graph's own drawing, for the README and the demo."""
    with open_graph() as g:
        return g.get_graph().draw_mermaid()


if __name__ == "__main__":
    print(mermaid())
