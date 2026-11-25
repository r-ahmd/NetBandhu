"""Build the knowledge base: load → split by heading → embed → write.

    uv run python -m kb.ingest            # build (or update) the collection
    uv run python -m kb.ingest --reset    # drop it first
    uv run python -m kb.ingest --check    # what is in it now

Run it twice and nothing changes: every chunk's id is derived from WHERE it came
from (source, section, ordinal), so a second run overwrites each point with itself
instead of adding a copy. That is the whole of idempotency here, and the script
prints the point count so you can watch it hold.

WHY ONE DOCUMENT PER `## ` SECTION
    Every policy document here is written with numbered subsections, and a
    citation has to name one: `[SLA · 3.2]`. Splitting on `##` headings and keeping
    the heading in the text gives a chunk that says what it is about, and gives
    the metadata a citation needs. A corpus this small does not need a second
    split, which is why CHUNK_SIZE only trims a section that overruns.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import uuid
from pathlib import Path

import yaml
from langchain_core.documents import Document
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter

from core.config import settings
from kb import store

# Fixed forever: change it and every point id changes, so every chunk duplicates.
NAMESPACE = uuid.UUID("6f1c2a10-0000-4000-8000-0000000000b1")

# The document title a citation shows. `[SLA · 3.2]` rather than
# `[service-level-agreement.md · 3.2]`: the reader is a subscriber.
CITE_NAMES = {
    "terms-of-service": "Terms",
    "service-level-agreement": "SLA",
    "fair-usage-policy": "FUP",
    "fee-schedule": "Fees",
}

AUTHORITY = {
    "terms-of-service": "operator",
    "service-level-agreement": "operator",
    "fair-usage-policy": "operator",
    "fee-schedule": "operator",
}


def cite_name(source_stem: str) -> str:
    return CITE_NAMES.get(source_stem, source_stem)


def load_document(path: Path) -> list[Document]:
    """One Document per `## ` section, the heading kept in the text and recorded.

    `strip_headers=False` keeps `## 3. Service credits` in the chunk body, so a
    passage read on its own still says which clause it belongs to. The heading is
    also parsed into metadata, which is what the citation label is built from.
    """
    text = path.read_text(encoding="utf-8")
    splitter = MarkdownHeaderTextSplitter(
        [("#", "doc_title"), ("##", "section"), ("###", "sub")],
        strip_headers=False)
    docs = splitter.split_text(text)

    out = []
    for i, d in enumerate(docs, start=1):
        # The CITATION wants the deepest numbered heading available: "### 3.2 How
        # the credit is calculated" must label as [SLA · 3.2], not [SLA · 3].
        # `sub` is the ### heading, `section` the ## one; fall back in that order.
        heading = d.metadata.get("sub") or d.metadata.get("section") or "preamble"
        number = re.match(r"\s*(\d+(?:\.\d+)*)", heading)
        section_id = number.group(1) if number else heading.strip()

        body = d.page_content.strip()
        if len(body) > settings.CHUNK_SIZE:
            body = RecursiveCharacterTextSplitter(
                chunk_size=settings.CHUNK_SIZE,
                chunk_overlap=settings.CHUNK_OVERLAP,
                separators=["\n\n", "\n", ". ", " "],
            ).split_text(body)[0]

        if "preamble" in (d.metadata.get("section", ""), d.metadata.get("sub", "")) or \
                section_id == "preamble":
            continue          # the document's own introduction: real, but it
                              # answers nothing a subscriber asks and it pollutes
                              # the top-k with a summary of the whole file

        key = f"{path.stem}|{section_id}|{i}"
        out.append(Document(
            page_content=body,
            metadata={
                "source": path.stem,
                "document": cite_name(path.stem),
                "section": section_id,
                "authority": AUTHORITY.get(path.stem, "operator"),
                "ordinal": i,
                "point_id": str(uuid.uuid5(NAMESPACE, key)),
            },
        ))
    return [d for d in out if d.page_content.strip()]


def load_corpus(directory: Path | None = None) -> list[Document]:
    directory = directory or settings.CORPUS_DIR
    docs: list[Document] = []
    for path in sorted(directory.glob("*.md")):
        docs.extend(load_document(path))
    if not docs:
        raise SystemExit(f"no .md files in {directory}")
    return docs


def build(reset: bool = False) -> int:
    settings.ensure_dirs()
    if reset:
        store.reset_collection()
        print("collection reset")

    docs = load_corpus()
    client = store.ensure_collection()

    # One upsert of pre-computed vectors, so ids are ours and a re-run overwrites.
    vectors = store.embeddings().embed_documents([d.page_content for d in docs])
    from qdrant_client import models as qm
    # QdrantVectorStore reads two payload keys, and only these two: the text from
    # `page_content` and the metadata dict from `metadata`. Anything else in the
    # payload is invisible to LangChain, which is why an earlier version of this
    # file returned documents with no `document`/`section` in their metadata and
    # every citation came out as "[doc · ?]".
    points = [
        qm.PointStruct(
            id=d.metadata["point_id"],
            vector=vec,
            payload={"page_content": d.page_content, "metadata": d.metadata},
        )
        for d, vec in zip(docs, vectors)
    ]
    client.upsert(collection_name=settings.collection, points=points)

    count = client.count(settings.collection).count
    print(f"{len(docs)} chunks from {len({d.metadata['source'] for d in docs})} documents")
    print(f"collection {settings.collection!r} now holds {count} points")
    # Embedded Qdrant holds a file lock. Closing it here is what lets the next
    # command (a search, the graph) open the same folder without waiting.
    client.close()
    return count


def check() -> None:
    client = store.ensure_collection()
    count = client.count(settings.collection).count
    print(f"collection {settings.collection!r}: {count} points, "
          f"{settings.embed_dim} dimensions")
    if count == 0:
        print("empty — run: uv run python -m kb.ingest")
        return
    rows = client.scroll(settings.collection, limit=100, with_payload=True, with_vectors=False)[0]
    per_doc: dict[str, int] = {}
    for r in rows:
        meta = r.payload.get("metadata", {})
        per_doc[meta.get("document", "?")] = per_doc.get(meta.get("document", "?"), 0) + 1
    for name, n in sorted(per_doc.items()):
        print(f"  {name:10s} {n:3d} chunks")
    print("\nsample citations:")
    for r in rows[:4]:
        meta, text = r.payload.get("metadata", {}), r.payload.get("page_content", "")
        print(f"  [{meta.get('document')} · {meta.get('section')}]  "
              f"{text[:60].replace(chr(10), ' ')}...")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Build the NetBandhu knowledge base")
    ap.add_argument("--reset", action="store_true", help="drop the collection first")
    ap.add_argument("--check", action="store_true", help="report what is in it")
    args = ap.parse_args()
    if args.check:
        check()
    else:
        build(reset=args.reset)
