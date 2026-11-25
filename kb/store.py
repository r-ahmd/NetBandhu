"""One door to the knowledge base: the embedding model, the collection, the search.

    from kb.store import embeddings, vector_store, search
    hits = search("what do I get for downtime?")     # list[Document], labelled

FOUR THINGS LIVE HERE, AND NOTHING ELSE DOES
    embeddings()    the model named by settings.EMBED_MODEL
    open_client()   embedded Qdrant on disk (one process at a time — it locks)
    vector_store()  LangChain's view of the collection
    search()        the one retrieval call the graph and the tools both use

WHY ONE COLLECTION PER EMBEDDING SPACE
    Two embedding models place the same sentence in different coordinates. A
    collection holding both returns neighbours that were never near each other,
    and the only symptom is worse answers. The collection name is derived from the
    model and its dimensions (`settings.collection`), so mixing them is not a
    mistake you can make by hand — you would have to rename a collection.
"""
from __future__ import annotations

import logging
from functools import lru_cache

from langchain_core.embeddings import Embeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient, models

from core.config import settings
from core.exceptions import KnowledgeBaseMissing

# pypdf warns per page for fonts it can only partly decode. The text still comes
# out, so from here on it is noise.
logging.getLogger("pypdf").setLevel(logging.ERROR)
logging.getLogger("qdrant_client").setLevel(logging.WARNING)


class FastEmbedEmbeddings(Embeddings):
    """The free fallback: a small model on your CPU, through fastembed.

    Five lines of adapter rather than a dependency on langchain-community, which
    would pull a much larger tree for the same two methods."""

    def __init__(self, model_name: str):
        from fastembed import TextEmbedding
        self.model = TextEmbedding(model_name)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [v.tolist() for v in self.model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        return next(iter(self.model.query_embed(text))).tolist()


@lru_cache
def embeddings() -> Embeddings:
    """The embedding model for the knowledge base: the same one, always.

    Cached because search() calls this on every retrieval and building the client
    (or loading a fastembed model) each time is waste."""
    if settings.embed_provider == "fastembed":
        return FastEmbedEmbeddings(settings.embed_name)
    from langchain.embeddings import init_embeddings
    return init_embeddings(f"google_genai:{settings.embed_name}",
                           output_dimensionality=settings.embed_dim)


@lru_cache
def open_client() -> QdrantClient:
    """Embedded Qdrant on disk: persistent, no server, no signup.

    ONE PROCESS AT A TIME. Embedded Qdrant locks the folder; a kernel or a second
    script holding it makes ingest fail with "Storage folder ... is already
    accessed by another instance". Close it, or wait for the other process."""
    settings.QDRANT_PATH.mkdir(parents=True, exist_ok=True)
    return QdrantClient(path=str(settings.QDRANT_PATH))


def collection_exists(client: QdrantClient | None = None) -> bool:
    client = client or open_client()
    try:
        client.get_collection(settings.collection)
        return True
    except Exception:
        return False


def ensure_collection(client: QdrantClient | None = None) -> QdrantClient:
    """Create the collection on first use, so ingest never needs a pre-step."""
    client = client or open_client()
    if not collection_exists(client):
        client.create_collection(
            collection_name=settings.collection,
            vectors_config=models.VectorParams(
                size=settings.embed_dim, distance=models.Distance.COSINE),
        )
    return client


@lru_cache
def _store() -> QdrantVectorStore:
    client = ensure_collection()
    return QdrantVectorStore(
        client=client,
        collection_name=settings.collection,
        embedding=embeddings(),
    )


def vector_store() -> QdrantVectorStore:
    if not collection_exists():
        raise KnowledgeBaseMissing(settings.collection)
    return _store()


def search(query: str, k: int | None = None) -> list:
    """The passages nearest this question, each carrying its citation metadata.

    `score_threshold` is deliberately absent: a threshold chosen by feel silently
    refuses good answers. The grader in the corrective loop decides relevance,
    and it can be scored (evals/rag_eval.yml) — which a hard-coded cut-off cannot.
    """
    store = vector_store()
    return store.similarity_search(query, k=k or settings.RAG_K)


def reset_collection() -> None:
    """Drop and recreate: what `kb/ingest.py --reset` does."""
    client = open_client()
    if collection_exists(client):
        client.delete_collection(settings.collection)
    ensure_collection(client)
    _store.cache_clear()
