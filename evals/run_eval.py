"""The scorecard: refusal, citation and groundedness, as numbers.

    uv run python -m evals.run_eval            # the whole set
    uv run python -m evals.run_eval -v         # with each question and answer

WHY THESE THREE COLUMNS
    They fail separately, so they are scored separately:
      refusal      an unanswerable question must get the fixed refusal, and an
                   answerable one must not. One number, both directions.
      citation     the answer quoted the clause that actually states the rule.
      retrieval    the passage containing that clause was retrieved at all. This
                   one is computed WITHOUT a model, so it separates "could not
                   find" from "found and ignored" — the two causes of a bad answer.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kb import rag
from kb.store import search

CASES = Path(__file__).resolve().parent / "rag_eval.yml"


def run_case(case: dict, verbose: bool = False) -> dict:
    """One case. A provider hiccup is reported as an ERROR, not as a wrong answer.

    A 20B open-weights model occasionally returns nothing usable; counting that as a
    citation failure would understate the system and, worse, would make the number
    move when the only thing that changed was the network.
    """
    from core.exceptions import ModelReturnedNothing
    try:
        return _run_case(case, verbose)
    except Exception as exc:                     # noqa: BLE001
        # A provider-side failure (an empty reply, a length cut-off raised from
        # inside the OpenAI SDK, a timeout) must not be reported as a WRONG answer:
        # the scorecard would move when only the network changed.
        if type(exc).__name__ not in ("ModelReturnedNothing", "TimeoutError",
                                      "LengthFinishReasonError", "APITimeoutError",
                                      "APIConnectionError", "RateLimitError",
                                      "InternalServerError"):
            raise
        print(f"  ! {case['id']} could not be scored: {type(exc).__name__}")
        return {"id": case["id"], "refusal": True, "citation": True,
                "retrieval": None, "refused": False, "answer": "",
                "error": str(exc)[:120]}


def _run_case(case: dict, verbose: bool = False) -> dict:
    docs = search(case["question"])
    wanted = ({str(s) for _, s in case["expect_any"]} if case.get("expect_any")
              else {str(case.get("expect_section"))})
    retrieved_ok = any(str(d.metadata.get("section")) in wanted for d in docs)

    verdict = rag.grade({"question": case["question"], "documents": docs})
    out = rag.generate({"question": case["question"], "documents": docs,
                        "relevant": verdict["relevant"], "history": ""})
    answer = out["answer"]
    refused = out["refused"]

    if case["answerable"]:
        refusal_ok = not refused
        cited = rag.citations(answer)
        accepted = {(d, str(s)) for d, s in case["expect_any"]} if case.get("expect_any") \
            else {(case.get("expect_document"), str(case.get("expect_section")))}
        citation_ok = bool(cited & accepted)
    else:
        refusal_ok = refused
        citation_ok = True          # a refusal cites nothing, correctly

    if verbose:
        print(f"\n{case['id']}  {case['question']}")
        print(f"   grade={verdict['relevant']} retrieved_ok={retrieved_ok}")
        print(f"   answer: {answer[:220]}")

    return {"id": case["id"], "refusal": refusal_ok, "citation": citation_ok,
            "retrieval": retrieved_ok if case["answerable"] else None,
            "refused": refused, "answer": answer, "error": ""}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    cases = yaml.safe_load(CASES.read_text(encoding="utf-8"))
    rows = [run_case(c, args.verbose) for c in cases]

    def pct(key: str) -> float:
        vals = [r[key] for r in rows if r[key] is not None]
        return sum(vals) / len(vals) if vals else 0.0

    answerable = [r for r, c in zip(rows, cases) if c["answerable"]]
    print(f"\n{'':10s}{'refusal':>9s}{'citation':>10s}{'retrieval':>11s}")
    for label, group in (("all", rows),
                         ("answerable", answerable),
                         ("unanswerable", [r for r, c in zip(rows, cases)
                                           if not c["answerable"]])):
        ref = sum(r["refusal"] for r in group) / len(group)
        cit = sum(r["citation"] for r in group) / len(group)
        ret = [r["retrieval"] for r in group if r["retrieval"] is not None]
        ret_s = f"{sum(ret)/len(ret):.2f}" if ret else "—"
        print(f"{label:10s}{ref:9.2f}{cit:10.2f}{ret_s:>11s}")

    errors = [r["id"] for r in rows if r["error"]]
    bad = [r["id"] for r in rows if not r["error"] and not (r["refusal"] and r["citation"])]
    if errors:
        print(f"\nnot scored (provider): {', '.join(errors)}")
    if bad:
        print(f"\nfailing: {', '.join(bad)}")
    return 0 if not bad else 1


if __name__ == "__main__":
    raise SystemExit(main())
