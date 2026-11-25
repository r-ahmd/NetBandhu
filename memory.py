"""Long-term memory: what the desk knows about a subscriber across conversations.

    from memory import open_memory, remember, recall

    with open_memory() as store:
        remember(store, "SUB-2041", "works nights, prefers contact after 6pm")
        recall(store, "SUB-2041", query="when can you call me?")

TWO MEMORIES, TWO PLACES (S11 §12)
    The CHECKPOINTER remembers one conversation — a thread. This STORE remembers a
    PERSON across every thread they ever open. Different lifetimes, different
    namespaces, and confusing them is how one subscriber's preference reaches
    another's chat.

NAMESPACE COMES FROM THE LOGIN, NEVER FROM THE TEXT
    `(subscriber_id,)` is built from the runtime identity the caller supplies. A
    memory keyed from anything inside the conversation could be written into
    someone else's folder by typing an id.

WHY IT IS OPTIONAL (E3)
    The two electives the proposal depends on are E1 (corrective RAG) and E2
    (durable pause). Memory is a third, kept because a support desk that forgets
    "I work nights" every Monday is worse than one that remembers. If it ever
    fights the deadline, this file and the `recall` node are what gets cut — the
    demo works without them.
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from langgraph.store.base import BaseStore
from langgraph.store.sqlite import SqliteStore

from core.config import settings
from models import build_model, stamp
from prompts.loader import load_prompts
from schemas import MemoryNote

PROMPTS = load_prompts("memory")


def namespace(subscriber_id: str) -> tuple[str, str]:
    """Where one subscriber's memories live. One tuple, built from the login."""
    return ("memories", subscriber_id)


@contextmanager
def open_memory() -> Iterator[BaseStore]:
    """The Store on disk, with a semantic index over the memory text."""
    settings.MEMORY_DB.parent.mkdir(parents=True, exist_ok=True)
    # isolation_level=None: SqliteStore manages its own transactions, and pysqlite's
    # implicit BEGIN is what produced "cannot start a transaction within a transaction".
    conn = sqlite3.connect(str(settings.MEMORY_DB), check_same_thread=False,
                           isolation_level=None)
    try:
        store = SqliteStore(conn)
        store.setup()          # create the tables once; idempotent
        yield store
    finally:
        conn.close()


def remember(store: BaseStore, subscriber_id: str, text: str, key: str | None = None) -> str:
    """Write one memory. Returns its key."""
    import uuid
    key = key or str(uuid.uuid4())[:8]
    store.put(namespace(subscriber_id), key, {"text": text})
    return key


def recall(store: BaseStore, subscriber_id: str, limit: int = 5) -> list[str]:
    """Everything on file for this subscriber, newest first."""
    items = store.search(namespace(subscriber_id), limit=limit)
    return [i.value.get("text", "") for i in items if i.value.get("text")]


def extract(message: str, reply: str, subscriber_id: str) -> MemoryNote:
    """Ask the model what, if anything, this turn taught us about the person.

    Most turns teach nothing — a question about an outage is about the incident,
    not the human. The prompt says so, and an empty `text` is the common answer.
    """
    from models import ask_json
    prompt = PROMPTS["remember_prompt"].format(
        exchange=f"Subscriber: {message}\nDesk: {reply}")
    model = build_model()
    note, _ = ask_json(model, MemoryNote, [("system", prompt)], context=None)
    return note


if __name__ == "__main__":
    with open_memory() as store:
        k = remember(store, "SUB-2041", "works nights, prefers contact after 6pm")
        print("wrote memory", k)
        print("recall:", recall(store, "SUB-2041"))
        print("another subscriber sees:", recall(store, "SUB-2077") or "(nothing)")
