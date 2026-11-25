"""The errors NetBandhu raises on purpose.

    NetBandhuError          the base: this is ours, and we meant it
    ├── KnowledgeBaseMissing    a collection was searched before kb/ingest.py built it
    ├── CorpusMiss             no passage cleared the grader's threshold
    ├── NotAuthorised          the record belongs to another subscriber
    └── ModelReturnedNothing   the call came back with no usable answer

WHY A BASE CLASS
    `KeyError: 'SUB-9999'` in a traceback tells you a dict lookup failed somewhere.
    `SubscriberNotFound("SUB-9999")` tells you which lookup, for which record, and
    that the answer is one of ours rather than a bug. One base means one `except`
    clause can tell deliberate failures from programming errors — and programming
    errors keep raising, loudly, because a silent `except Exception` around a graph
    is how a wrong refund reaches a subscriber.
"""
from __future__ import annotations


class NetBandhuError(Exception):
    """Base for every error NetBandhu raises deliberately."""


class KnowledgeBaseMissing(NetBandhuError):
    """The vector collection is absent: `uv run python -m kb.ingest` builds it."""

    def __init__(self, collection: str):
        super().__init__(
            f"knowledge base {collection!r} does not exist yet — "
            f"build it with: uv run python -m kb.ingest")


class CorpusMiss(NetBandhuError):
    """No passage in the corpus answers the question, after the corrective loop.

    Raised so the caller must say the fixed refusal rather than improvise one."""


class NotAuthorised(NetBandhuError):
    """The record belongs to someone other than the logged-in subscriber."""


class SubscriberNotFound(NetBandhuError):
    """No subscriber with that id or phone number in the backend."""


class IncidentNotFound(NetBandhuError):
    """No outage with that incident id."""


class ModelReturnedNothing(NetBandhuError):
    """The call came back empty, cut off, or blocked by the provider's filter."""


class AlreadyCredited(NetBandhuError):
    """This (subscriber, incident) pair has already been paid — not an error to
    retry, an outcome to report. Idempotency by ledger, not by hope."""


class BelowThreshold(NetBandhuError):
    """Downtime did not reach the SLA §3.2 threshold, so no credit is due."""
