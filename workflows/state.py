"""What the graph remembers while it works on ONE turn.

RAW DATA ONLY. No prompt text, no model objects: a checkpoint writes this dict to
SQLite on every super-step, so anything unpicklable here breaks resumability — and
resumability is the feature (E2).

`cited` accumulates: the policy desk and the credit desk can both add labels, and a
turn that cites two clauses should show both. A list with no reducer is fine as
long as one node writes it per super-step, so `compose` is the single writer.
"""
from __future__ import annotations

from typing import Annotated, TypedDict

from langgraph.graph.message import add_messages


class DeskState(TypedDict, total=False):
    # in: from the CLI or the API, one per turn
    message: str                  # what the subscriber typed
    messages: Annotated[list, add_messages]   # the thread's conversation
    subscriber_id: str            # from the login, NEVER from the message
    actor: str                    # "subscriber" | "supervisor" — who is typing

    # worked out inside
    history: str                  # a compact transcript for rewriting and composing
    intent: dict                  # Intent.model_dump()
    route: str                    # the conditional edge's key
    question: str                 # the standalone question RAG searches and answers
    query: str                    # what retrieve actually searches (rewrite overwrites)
    documents: list               # list[Document]
    relevant: bool
    grade_reason: str
    attempts: int                 # searches so far in the corrective loop

    # the money path
    verdict: dict                 # SlaVerdict.model_dump() — computed by check_sla
    proposal: dict                # CreditProposal.model_dump()
    decision: dict                # ManagerDecision.model_dump(), after resume
    outcome: dict                 # what apply_credit wrote (or why nothing was)

    # out
    answer: str
    cited: list                   # [[document, section], ...]
    refused: bool
    progress: list                # one line per node that did real work
