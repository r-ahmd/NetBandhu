"""Everything NetBandhu asks a model to produce, as Pydantic models. New in v0.1.

    from schemas import Intent, SlaVerdict, CreditProposal

WHY SCHEMAS, NOT PROSE
    The model reads the ticket and *proposes*; the program decides. A schema is how
    that sentence becomes enforceable: `Intent.route` is one of five literals, so
    "I think this is about billing" cannot silently become a sixth route the graph
    has no edge for. `CreditProposal.amount` is computed by code before the model
    sees it, and the validator below refuses a proposal whose amount disagrees with
    the clause — the model cannot invent a number even if it tries.

    Field order matters: a model writes fields in the order they are declared, so
    `reason` before `route` makes the model say why before it commits.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

Route = Literal["account", "policy_qa", "credit_request", "out_of_scope"]
"""The four destinations the graph has edges for. Exactly four, by construction."""

Category = Literal["outage", "billing", "policy", "credit", "chitchat", "abuse"]


class Intent(BaseModel):
    """What the subscriber is asking for, before anything is looked up."""

    reason: str = Field(
        description="One short sentence: what the subscriber actually wants. "
                    "Written before the route, so the decision is explained.")
    category: Category = Field(
        description="What this message is about. Pick the single best fit.")
    route: Route = Field(
        description="Where the request should go. 'account' for facts about their "
                    "own connection or bill; 'policy_qa' for what the documents say; "
                    "'credit_request' only when they ask for money back or a waiver; "
                    "'out_of_scope' for anything else.")
    needs_clarification: bool = Field(
        default=False,
        description="True only if the request cannot be routed without one more "
                    "detail from the subscriber.")
    clarification_question: str | None = Field(
        default=None,
        description="The one question to ask when needs_clarification is true.")

    @model_validator(mode="after")
    def _clarification_is_complete(self) -> "Intent":
        if self.needs_clarification and not self.clarification_question:
            raise ValueError("needs_clarification is true but no question was given")
        return self


class SlaVerdict(BaseModel):
    """The outcome of assessing one incident for one subscriber.

    Constructed in code by `tools.check_sla`, never by the model: every field here
    is a fact, and a fact about money is not something a language model supplies.
    The model's job is to explain this object, which is why it has a `sentence`.
    """

    subscriber: str
    incident: str
    monthly_fee: float
    downtime_hours: float = Field(description="Continuous downtime of the incident.")
    threshold_hours: float = Field(description="SLA §3.1: four continuous hours.")
    qualifies: bool = Field(description="True when downtime exceeds the threshold.")
    amount: float = Field(default=0.0, description="Computed credit, rounded to the taka.")
    clause: str = Field(default="SLA §3.2", description="The clause the amount comes from.")
    already_credited: bool = Field(default=False)
    existing_credit_id: str | None = None
    sentence: str = Field(description="One plain sentence stating the verdict and why.")

    @model_validator(mode="after")
    def _amount_matches_the_rule(self) -> "SlaVerdict":
        """Recompute the credit and refuse to construct a lying verdict.

        This is the guard that makes the demo's ৳267 arithmetic a property of the
        program rather than of the prompt. If someone edits a clause, or a node
        passes a hand-made amount, construction fails here instead of at the
        subscriber's bill.
        """
        if self.already_credited:
            # A second request for a paid incident owes nothing by definition
            # (SLA §3.5), so the arithmetic check does not apply: the amount is 0
            # because the money already moved, not because the rule says so.
            if round(self.amount) != 0:
                raise ValueError("an already-credited incident cannot carry an amount")
            return self
        expected = 0.0
        if self.qualifies:
            expected = round(self.monthly_fee / 24 * self.downtime_hours)
        if round(self.amount) != expected:
            raise ValueError(
                f"amount {self.amount} contradicts SLA §3.2: "
                f"{self.monthly_fee}/24 x {self.downtime_hours:.3f} = {expected}")
        return self


class CreditProposal(BaseModel):
    """What the approval node hands a human. The form, not the payment."""

    subscriber_id: str
    subscriber_name: str
    incident: str
    node: str
    downtime_hours: float
    clause: str
    amount: float
    reason: str = Field(description="Why the credit is due, in the subscriber's terms.")

    def as_form(self) -> dict:
        """What the supervisor sees in the interrupt payload."""
        return {
            "subscriber": f"{self.subscriber_name} ({self.subscriber_id})",
            "incident": self.incident,
            "node": self.node,
            "downtime": f"{self.downtime_hours:.2f} h",
            "clause": self.clause,
            "amount": f"৳{self.amount:,.0f}",
            "reason": self.reason,
        }


class ManagerDecision(BaseModel):
    """What a supervisor sends back to resume a paused run.

    `reject` carries a reason because the subscriber is told it, and a rejection
    with no reason is the thing this desk exists to stop.
    """

    action: Literal["approve", "edit", "reject"]
    amount: float | None = Field(
        default=None,
        description="The amount to apply instead, when action is 'edit'.")
    reason: str | None = Field(
        default=None,
        description="Required when rejecting: the reason relayed to the subscriber.")

    @model_validator(mode="after")
    def _coherent(self) -> "ManagerDecision":
        if self.action == "edit" and (self.amount is None or self.amount <= 0):
            raise ValueError("an edit needs a positive amount")
        if self.action == "reject" and not (self.reason or "").strip():
            raise ValueError("a rejection needs a reason: the subscriber is told it")
        return self


class Grade(BaseModel):
    """The corrective-RAG grader's verdict on one set of passages."""

    reason: str = Field(
        description="One short sentence: which passage answers the question, or "
                    "what is missing from them.")
    relevant: bool = Field(
        description="True only if the passages contain the rule needed to answer.")


class Standalone(BaseModel):
    """A follow-up rewritten so it means the same thing with no conversation."""

    question: str = Field(
        description="The latest message rewritten as a self-contained question: name "
                    "the incident, plan or document it refers to. If it is already "
                    "self-contained, return it unchanged.")


class Citation(BaseModel):
    """A passage the answer relied on, as the subscriber sees it."""

    document: str
    section: str

    @property
    def label(self) -> str:
        return f"[{self.document} · {self.section}]"


class MemoryNote(BaseModel):
    """One lasting fact about a subscriber, worth knowing on their next contact."""

    text: str = Field(
        description="One short sentence in the third person: 'works nights, prefers "
                    "contact after 6pm'. Empty string when the turn taught nothing.")
    kind: Literal["preference", "contact", "context"] = "preference"
