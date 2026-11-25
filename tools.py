"""The five tools — the only place a fact about an account comes from.

    from tools import get_subscription, get_invoice, check_outage, check_sla, propose_credit

WHY TOOLS AND NOT A PROMPT
    A subscriber's plan, their invoice and an incident's window live in records. A
    model that recites them from memory is guessing about someone's money. Every
    fact the desk states about an account is the return value of one of these.

THE ONE RULE, AND WHERE IT IS ENFORCED
    `check_sla` computes the credit IN CODE and returns a validated `SlaVerdict`
    whose validator recomputes the amount and refuses to construct a lying one. The
    model never supplies a number; it explains a number it was handed. That is why
    the demo can say ৳267 and be right.

`propose_credit` WRITES NOTHING
    It returns a proposal for a human to decide on. The only writer is
    `apply_credit`, which the graph calls after `interrupt()` returns an approval.
    A tool the model can call must not be able to move money.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml
from langchain_core.tools import tool

from core.config import settings
from core.exceptions import (AlreadyCredited, BelowThreshold, IncidentNotFound,
                             NotAuthorised, SubscriberNotFound)
from schemas import CreditProposal, SlaVerdict


@lru_cache
def backend() -> dict:
    """The mock backend, read once. A real system would be a database behind this."""
    return yaml.safe_load(settings.DATA_FILE.read_text(encoding="utf-8"))


def _ledger() -> list[dict]:
    """Credits already applied: the seed in the backend plus the runtime ledger.

    SLA §3.5 is one credit per incident, so (subscriber, incident) is the key. A
    ledger rather than a flag on the incident, because the incident is the operator's
    and the credit is the subscriber's.
    """
    rows = list(backend().get("credits", []))
    path: Path = settings.CREDIT_LEDGER
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def find_credit(subscriber_id: str, incident_id: str) -> dict | None:
    for c in _ledger():
        if c["subscriber"] == subscriber_id and c["incident"] == incident_id:
            return c
    return None


def apply_credit(subscriber_id: str, incident_id: str, amount: float,
                 clause: str, approved_by: str) -> dict:
    """The ONLY writer of a credit. Idempotent on (subscriber, incident).

    Called by the graph after approval, never by the model. Appends one line to
    the ledger, so a second call for the same incident finds the first and refuses
    — which is what makes the pause-then-resume test pass: resuming twice cannot
    pay twice.
    """
    existing = find_credit(subscriber_id, incident_id)
    if existing:
        raise AlreadyCredited(
            f"{subscriber_id} already credited ৳{existing['amount']:,.0f} for "
            f"{incident_id} (credit {existing['id']})")
    rows = _ledger()
    record = {
        "id": f"CR-{1001 + len(rows)}",
        "subscriber": subscriber_id,
        "incident": incident_id,
        "amount": round(float(amount), 2),
        "clause": clause,
        "approved_by": approved_by,
        "applied": "runtime",
    }
    settings.CREDIT_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with settings.CREDIT_LEDGER.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record) + "\n")
    return record


def _subscriber(subscriber_id_or_phone: str) -> tuple[str, dict]:
    """Resolve an id or a phone number to (id, record). The desk is told both."""
    key = (subscriber_id_or_phone or "").strip()
    subs = backend()["subscribers"]
    if key in subs:
        return key, subs[key]
    for sid, rec in subs.items():
        if rec.get("phone") == key:
            return sid, rec
    raise SubscriberNotFound(key)


def _incident(incident_id: str) -> dict:
    inc = backend()["outages"].get((incident_id or "").strip().upper())
    if not inc:
        raise IncidentNotFound(incident_id)
    return inc


def downtime_hours(incident: dict, now: str | None = None) -> float:
    """Continuous downtime of an incident, in hours.

    A live incident (`restored: null`) is measured to `now`, which the caller
    supplies — a demo needs a fixed clock, not the wall clock, or the amount on
    screen changes between rehearsal and demo.
    """
    from datetime import datetime

    start = datetime.fromisoformat(incident["raised"])
    end = datetime.fromisoformat(incident["restored"] or now or incident["eta"])
    return round((end - start).total_seconds() / 3600, 4)


# ── the five tools ───────────────────────────────────────────────────────────
@tool(parse_docstring=True)
def get_subscription(subscriber_id_or_phone: str) -> dict:
    """Look up a subscriber's plan, node and account status.

    Use this first for any question about a subscriber's own connection: it returns
    the plan (and its monthly fee) and the node they are served from, which the
    outage tool needs.

    Args:
        subscriber_id_or_phone: the subscriber id (SUB-nnnn) or the phone number
            they signed up with.
    """
    sid, rec = _subscriber(subscriber_id_or_phone)
    plan = backend()["plans"][rec["plan"]]
    return {
        "subscriber_id": sid,
        "name": rec["name"],
        "plan": rec["plan"],
        "plan_name": plan["name"],
        "speed_mbps": plan["speed_mbps"],
        "monthly_fee": plan["monthly_fee"],
        "node": rec["node"],
        "status": rec["status"],
        "since": rec["since"],
    }


@tool(parse_docstring=True)
def get_invoice(subscriber_id: str, period: str = "") -> dict:
    """Return a subscriber's invoice with every line item and the total.

    Use this when the subscriber asks why a bill is a particular amount. The line
    items are the answer: a plan charge and any fee or credit, each named.

    Args:
        subscriber_id: the subscriber id (SUB-nnnn).
        period: the billing period as YYYY-MM, e.g. "2026-06". Empty returns the
            most recent invoice on file.
    """
    sid, _ = _subscriber(subscriber_id)
    invoices = [(iid, inv) for iid, inv in backend()["invoices"].items()
                if inv["subscriber"] == sid]
    if period:
        invoices = [(iid, inv) for iid, inv in invoices if inv["period"] == period]
    if not invoices:
        return {"error": f"no invoice on file for {sid}"
                         + (f" in {period}" if period else "")}
    iid, inv = sorted(invoices, key=lambda kv: kv[1]["issued"])[-1]
    total = sum(item["amount"] for item in inv["items"])
    return {
        "invoice_id": iid,
        "period": inv["period"],
        "issued": inv["issued"],
        "due": inv["due"],
        "items": inv["items"],
        "total": total,
    }


@tool(parse_docstring=True)
def check_outage(node_or_area: str) -> dict:
    """Check for an outage on a node or in an area, live incidents first.

    Use this when a subscriber reports that their connection is down. It returns
    the incident id, when it started, the estimated fix and the cause. An empty
    `incidents` list means there is no known outage — which is information, not a
    dead end: say so and ask them to check their own equipment.

    Args:
        node_or_area: a node id (e.g. AGRABAD-N3) or an area name (e.g. Agrabad).
    """
    key = (node_or_area or "").strip().lower()
    hits = []
    for iid, inc in backend()["outages"].items():
        if key in (inc["node"].lower(), inc["area"].lower()):
            hits.append({
                "incident_id": iid,
                "node": inc["node"],
                "area": inc["area"],
                "raised": inc["raised"],
                "restored": inc["restored"],
                "eta": inc["eta"],
                "cause": inc["cause"],
                "live": inc["restored"] is None,
            })
    hits.sort(key=lambda h: (not h["live"], h["raised"]), reverse=False)
    return {"query": node_or_area, "incidents": hits,
            "note": "" if hits else "no incident on record for this node or area"}


@tool(parse_docstring=True)
def check_sla(incident_id: str, subscriber_id: str, now: str = "") -> dict:
    """Assess one incident against SLA §3.2 and return the credit that is due.

    THE AMOUNT IS COMPUTED HERE, NOT BY THE MODEL. The result carries the downtime,
    the four-hour threshold, the clause, and the amount rounding to the taka, and
    the schema refuses to construct a verdict whose amount disagrees with the rule.
    Call it for any question about what a subscriber is owed for an outage.

    Args:
        incident_id: the incident id (INC-nnnn) from check_outage.
        subscriber_id: the subscriber id (SUB-nnnn) the credit would go to.
        now: ISO timestamp to measure a LIVE incident against, e.g.
            "2026-06-14T14:00:00". Empty uses the incident's ETA.
    """
    inc = _incident(incident_id)
    sid, rec = _subscriber(subscriber_id)
    if sid not in inc["affected"]:
        raise NotAuthorised(
            f"{sid} is not on {incident_id}; that incident affects other subscribers")

    plan = backend()["plans"][rec["plan"]]
    hours = downtime_hours(inc, now or None)
    threshold = settings.CREDIT_THRESHOLD_HOURS
    qualifies = hours > threshold
    amount = round(plan["monthly_fee"] / 24 * hours) if qualifies else 0

    # The backend stores incidents keyed by id with no `id` field inside, so the
    # lookup key is the id the caller passed — normalised, because a model may
    # write "inc-8812".
    existing = find_credit(sid, incident_id.strip().upper())
    if existing:
        verdict = SlaVerdict(
            subscriber=sid, incident=incident_id.upper(), monthly_fee=plan["monthly_fee"],
            downtime_hours=hours, threshold_hours=threshold, qualifies=True, amount=0,
            already_credited=True, existing_credit_id=existing["id"],
            sentence=(f"{incident_id.upper()} has already been credited: "
                      f"৳{existing['amount']:,.0f} under {existing['clause']} "
                      f"(credit {existing['id']}). No further credit is due for it."),
        )
        return verdict.model_dump()

    if qualifies:
        sentence = (f"{incident_id.upper()} was down {hours:.2f} hours, which exceeds "
                    f"the {threshold:.0f}-hour threshold in SLA §3.1, so a credit of "
                    f"৳{amount:,.0f} is due under SLA §3.2.")
    else:
        sentence = (f"{incident_id.upper()} was down {hours:.2f} hours, which does not "
                    f"exceed the {threshold:.0f}-hour threshold in SLA §3.1, so no "
                    f"credit is due. A period longer than {threshold:.0f} hours would qualify.")

    verdict = SlaVerdict(
        subscriber=sid, incident=incident_id.upper(), monthly_fee=plan["monthly_fee"],
        downtime_hours=hours, threshold_hours=threshold, qualifies=qualifies,
        amount=amount, sentence=sentence,
    )
    return verdict.model_dump()


@tool(parse_docstring=True)
def propose_credit(subscriber_id: str, incident_id: str, amount: float,
                   reason: str) -> dict:
    """Propose a service credit for a billing supervisor to approve.

    This WRITES NOTHING and moves no money. It produces the form the supervisor
    sees: subscriber, incident, amount, clause. The credit is applied only if a
    supervisor approves, which is why calling this is always safe.

    Args:
        subscriber_id: the subscriber id (SUB-nnnn).
        incident_id: the incident id (INC-nnnn) the credit is for.
        amount: the amount in taka, as returned by check_sla. Do not compute it
            yourself and do not change it.
        reason: one sentence the supervisor will read, in the subscriber's terms.
    """
    sid, rec = _subscriber(subscriber_id)
    inc = _incident(incident_id)
    if sid not in inc["affected"]:
        raise NotAuthorised(f"{sid} is not affected by {incident_id}")
    plan = backend()["plans"][rec["plan"]]
    expected = round(plan["monthly_fee"] / 24 * downtime_hours(inc, None))
    if abs(float(amount) - expected) > 1:
        return {"error": f"amount {amount} does not match SLA §3.2 for this incident "
                         f"(expected {expected}). Call check_sla and use its amount."}

    proposal = CreditProposal(
        subscriber_id=sid, subscriber_name=rec["name"], incident=incident_id.upper(),
        node=inc["node"], downtime_hours=downtime_hours(inc, None),
        clause="SLA §3.2", amount=float(expected), reason=reason,
    )
    return {"status": "pending_supervisor_approval", "proposal": proposal.model_dump(),
            "note": "Nothing has been applied. A billing supervisor decides."}


READ_TOOLS = [get_subscription, get_invoice, check_outage, check_sla]
ALL_TOOLS = READ_TOOLS + [propose_credit]


def _selftest() -> int:
    """Assert the planted edge cases. `uv run python -m tools --selftest`."""
    checks = []

    v = check_sla.invoke({"incident_id": "INC-8809", "subscriber_id": "SUB-2150"})
    checks.append(("3h50m earns nothing", v["qualifies"] is False and v["amount"] == 0))
    checks.append(("and says when it would", "longer than 4 hours" in v["sentence"]))

    v = check_sla.invoke({"incident_id": "INC-8812", "subscriber_id": "SUB-2041",
                          "now": "2026-06-14T14:00:00"})
    checks.append(("5h20m earns 267", v["qualifies"] and v["amount"] == 267))
    checks.append(("and cites §3.2", v["clause"] == "SLA §3.2"))

    v = check_sla.invoke({"incident_id": "INC-8815", "subscriber_id": "SUB-2077"})
    checks.append(("4h10m on the 1800 plan earns 313",
                   v["qualifies"] and v["amount"] == 313))

    v = check_sla.invoke({"incident_id": "INC-8801", "subscriber_id": "SUB-2077"})
    checks.append(("already-credited is reported, not repaid",
                   v["already_credited"] and v["amount"] == 0))

    try:
        check_sla.invoke({"incident_id": "INC-8812", "subscriber_id": "SUB-2077"})
        checks.append(("another subscriber's incident is refused", False))
    except Exception:
        checks.append(("another subscriber's incident is refused", True))

    p = propose_credit.invoke({"subscriber_id": "SUB-2041", "incident_id": "INC-8812",
                               "amount": 267, "reason": "monsoon outage"})
    checks.append(("a proposal is pending, not applied",
                   p["status"] == "pending_supervisor_approval"))

    p = propose_credit.invoke({"subscriber_id": "SUB-2041", "incident_id": "INC-8812",
                               "amount": 999, "reason": "made up"})
    checks.append(("a wrong amount is refused", "error" in p))

    for name, ok in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    return 0 if all(ok for _, ok in checks) else 1


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        raise SystemExit(_selftest())
    print("tools:", ", ".join(t.name for t in ALL_TOOLS))
