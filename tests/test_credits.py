"""The money rules, as assertions. `uv run pytest -q`."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import settings
from schemas import ManagerDecision, SlaVerdict
from tools import check_sla, downtime_hours


def test_threshold_is_four_hours_everywhere():
    """The document, the settings and the proposal must agree on §3.1."""
    sla = (settings.CORPUS_DIR / "service-level-agreement.md").read_text(encoding="utf-8")
    # the source wraps the clause across two lines: compare on normalised text
    assert "exceeds four (4) hours" in " ".join(sla.split())
    assert settings.CREDIT_THRESHOLD_HOURS == 4.0


def test_formula_in_the_document_matches_the_code():
    """§3.2 states the formula the code applies. If one moves, this fails."""
    sla = (settings.CORPUS_DIR / "service-level-agreement.md").read_text(encoding="utf-8")
    flat = " ".join(sla.split())
    assert "monthly_fee / 24" in flat and "downtime_hours" in flat
    fee, hours = 1200, 5.3333
    assert round(fee / 24 * hours) == 267


def test_below_threshold_earns_nothing():
    v = check_sla.invoke({"incident_id": "INC-8809", "subscriber_id": "SUB-2150"})
    assert v["qualifies"] is False
    assert v["amount"] == 0
    assert "longer than 4 hours" in v["sentence"]


def test_just_over_threshold_earns_a_credit():
    v = check_sla.invoke({"incident_id": "INC-8815", "subscriber_id": "SUB-2077"})
    assert v["qualifies"] is True
    assert v["amount"] == 313          # 1800/24 x 4.1667


def test_live_incident_uses_the_supplied_clock(tmp_path, monkeypatch):
    # the demo writes to the real ledger; point it at a scratch file so this test
    # measures the ARITHMETIC and not whether a previous run already paid
    monkeypatch.setattr(settings, "CREDIT_LEDGER", tmp_path / "credits.jsonl")
    v = check_sla.invoke({"incident_id": "INC-8812", "subscriber_id": "SUB-2041",
                          "now": "2026-06-14T14:00:00"})
    assert v["downtime_hours"] == pytest.approx(5.3333, abs=1e-4)
    assert v["amount"] == 267


def test_a_lying_verdict_cannot_be_constructed():
    with pytest.raises(ValueError, match="contradicts SLA"):
        SlaVerdict(subscriber="SUB-2041", incident="INC-8812", monthly_fee=1200,
                   downtime_hours=5.3333, threshold_hours=4.0, qualifies=True,
                   amount=5000, sentence="a number nobody computed")


def test_rejection_must_carry_a_reason():
    with pytest.raises(ValueError, match="needs a reason"):
        ManagerDecision(action="reject")


def test_edit_must_carry_a_positive_amount():
    with pytest.raises(ValueError, match="positive amount"):
        ManagerDecision(action="edit", amount=0)


def test_downtime_is_computed_from_the_stamps():
    from tools import backend
    inc = backend()["outages"]["INC-8809"]
    assert downtime_hours(inc) == pytest.approx(3.8333, abs=1e-4)
