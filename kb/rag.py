"""Retrieval and the grounded answer: retrieve, grade, rewrite, compose.

    from kb.rag import retrieve, generate, REFUSAL, citations

    answer = generate({"question": "...", "documents": search(...)})["answer"]

THE SHAPE IS THE POINT
    `retrieve(state) -> {"documents": [...]}` and
    `generate(state) -> {"answer": "..."}`.
Each takes a dict and returns only the keys it adds — which is exactly the shape of
a LangGraph node, so the graph wires these two functions in without rewriting them.

THE CONTRACT (prompts/rag.yml, `rag_prompt`) AND WHO KEEPS IT
    Every answer must either carry at least one `[document · section]` label that
    it was given, or say the fixed refusal. `citations()` extracts the labels from a
    reply; `is_refusal()` recognises the refusal by pattern, not by exact string, so
    an eval does not fail because the model wrote "do not" for "don't".

WHY THE REFUSAL IS ONE FIXED SENTENCE
    It has to be detectable without a judge. A model that improvises a friendlier
    refusal each time cannot be scored, and a system that cannot be scored cannot
    be shown to have improved.
"""
from __future__ import annotations

import re
from functools import lru_cache

from langchain_core.documents import Document

from core.config import settings
from models import build_model, stamp
from prompts.loader import load_prompts
from schemas import Grade, Standalone

PROMPTS = load_prompts("rag")
RAG_PROMPT = PROMPTS["rag_prompt"]
RAG_VERSION = PROMPTS["rag_prompt_version"]
GRADE_PROMPT = PROMPTS["grade_prompt"]
GRADE_VERSION = PROMPTS["grade_prompt_version"]
REWRITE_PROMPT = PROMPTS["rewrite_prompt"]
REWRITE_VERSION = PROMPTS["rewrite_prompt_version"]

# The refusal, fixed. `is_refusal` matches the SHAPE so a contraction or an extra
# clause does not turn a refusal into an answer the eval scores as confident.
REFUSAL = ("Our documents don't cover that. A support engineer will follow up.")
_REFUSAL_RE = re.compile(
    r"our documents (?:don't|do not|dont) cover that", re.IGNORECASE)

LABEL_RE = re.compile(r"\[([A-Za-z][A-Za-z ]*?) · ([^\]]+)\]")


def label(doc: Document) -> str:
    """The citation label for a chunk: `[SLA · 3.2]`."""
    d = doc.metadata.get("document", doc.metadata.get("source", "doc"))
    s = doc.metadata.get("section", "?")
    return f"[{d} · {s}]"


def format_context(docs: list[Document]) -> str:
    """The passages, each prefixed with the only label the answer may quote."""
    blocks = []
    for i, d in enumerate(docs, start=1):
        blocks.append(f"--- passage {i} {label(d)} ---\n{d.page_content.strip()}")
    return "\n\n".join(blocks)


def is_refusal(text: str) -> bool:
    return bool(_REFUSAL_RE.search(text or ""))


# A reply that STARTED with the refusal and then kept talking is a refusal with an
# appendix, not an answer: the contract says the refusal is the whole reply, so the
# extra sentences are the model slipping past it. Treat it as the fixed sentence.
_LEADING_REFUSAL = re.compile(
    r"^\s*" + _REFUSAL_RE.pattern, re.IGNORECASE)


def normalise(text: str) -> str:
    """Collapse a refusal-with-extras into the fixed sentence; strip stray markup.

    Observed in the first eval run: one reply read
    "The late///<qualification: The late payment fee is ...", which is a decoding
    artefact of a reasoning model being cut off mid-thought. It is not an answer
    and must not be scored as one, so anything that does not parse as a clean,
    cited answer with a known label becomes the refusal.
    """
    t = (text or "").strip()
    if _LEADING_REFUSAL.search(t):
        return REFUSAL
    # a label the model wrapped in markdown, e.g. **[SLA · 3.2]**
    t = re.sub(r"\*\*(\[[^\]]+\])\*\*", r"\1", t)
    return t.strip()


def citations(text: str) -> set[tuple[str, str]]:
    """The (document, section) pairs a reply actually quoted."""
    return {(m.group(1).strip(), m.group(2).strip()) for m in LABEL_RE.finditer(text or "")}


def retrieve(state: dict) -> dict:
    """Search for the question in state. Returns only `documents`."""
    from kb.store import search
    question = (state.get("query") or state.get("question") or "").strip()
    if not question:
        return {"documents": []}
    return {"documents": search(question, k=state.get("k") or settings.RAG_K)}


def grade(state: dict) -> dict:
    """Does any passage contain the rule needed to answer? Returns `relevant`."""
    docs = state.get("documents") or []
    if not docs:
        return {"relevant": False, "grade_reason": "no passages retrieved"}
    model = build_model()
    prompt = GRADE_PROMPT.format(question=state["question"], context=format_context(docs))
    verdict, _ = __import__("models").ask_json(
        model, Grade, [("system", prompt)],
        context=None)
    return {"relevant": verdict.relevant, "grade_reason": verdict.reason}


def rewrite(state: dict) -> dict:
    """Rephrase the search in the corpus's own vocabulary, capped by the caller.

    The rewrite OVERWRITES `query`, not `question`: `generate` answers the
    subscriber's words, while `retrieve` searches the better ones."""
    model = build_model()
    prompt = REWRITE_PROMPT.format(question=state["question"], context=format_context(state.get("documents") or []))
    out, _ = __import__("models").ask_json(model, Standalone, [("system", prompt)], context=None)
    attempts = int(state.get("attempts", 1))
    return {"query": out.question.strip() or state["question"], "attempts": attempts + 1}


def after_grade(state: dict) -> str:
    """The conditional edge out of `grade`: answer, or search again, or give up."""
    if state.get("relevant"):
        return "generate"
    if int(state.get("attempts", 1)) >= settings.RAG_MAX_ATTEMPTS:
        return "generate"          # out of attempts: compose() will refuse
    return "rewrite"


def generate(state: dict) -> dict:
    """The grounded answer: a labelled passage, or the fixed refusal.

    `documents` empty or unmistakeably irrelevant short-circuits to the refusal
    without a model call: paying for a call that can only say "I don't know" is
    how a free tier is spent on nothing.
    """
    docs = state.get("documents") or []
    if not docs or state.get("relevant") is False:
        return {"answer": REFUSAL, "cited": [], "refused": True}

    model = build_model()
    prompt = RAG_PROMPT.format(context=format_context(docs), question=state["question"],
                               history=state.get("history") or "(no earlier turns)")
    reply = model.invoke([("system", prompt), ("human", state["question"])],
                         config=stamp("rag_prompt", RAG_VERSION, prompt))
    text = reply.content if isinstance(reply.content, str) else str(reply.content)
    text = text.strip()

    text = normalise(text)
    quoted = citations(text)
    known = {(str(d.metadata["document"]), str(d.metadata["section"])) for d in docs}

    # An answer with no label, a label it was not given, or a mangled body is not
    # an answer. The contract has two outcomes, so anything else is the refusal.
    if text == REFUSAL or is_refusal(text) or not quoted:
        return {"answer": REFUSAL, "cited": [], "refused": True}
    if not quoted <= known:
        # It cited something it was not given. That is a fabrication, and the
        # honest response is the refusal, not the invented clause.
        return {"answer": REFUSAL, "cited": [], "refused": True,
                "note": f"dropped a fabricated citation: {sorted(quoted - known)}"}
    return {"answer": text, "cited": sorted(quoted), "refused": False}


# ── v8's conversational rewrite (S11) — a follow-up as a standalone question ──
@lru_cache
def _rewrite_prompt_text() -> str:
    return load_prompts("rag")["standalone_prompt"]


def standalone(question: str, history: str) -> str:
    """A follow-up rewritten so it means the same thing with no conversation.

    "Am I owed anything for that?" is unanswerable alone; "What credit am I owed
    for the INC-8812 outage?" is searchable. If the rewrite fails, the original
    question is used: a worse query beats a crashed turn.
    """
    if not (history or "").strip():
        return question
    model = build_model()
    try:
        out, _ = __import__("models").ask_json(
            model, Standalone,
            [("system", _rewrite_prompt_text().format(history=history)),
             ("human", question)], context=None)
        return out.question.strip() or question
    except Exception:
        return question
