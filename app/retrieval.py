import re
from collections import Counter
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from threading import Lock
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
RUNBOOK_DIR = Path(__file__).resolve().parents[1] / "runbooks"
MIN_SIMILARITY = 0.30
MAX_CHUNK_WORDS = 120
_INDEX_LOCK = Lock()
HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)\s*#*\s*$")


@dataclass(frozen=True)
class RunbookChunk:
    source_id: str
    document: str
    title: str
    section: str
    text: str


def load_chunks(directory: Path) -> tuple[RunbookChunk, ...]:
    chunks = []
    for path in sorted(directory.glob("*.md")):
        # The corpus uses ATX headings and prose. Skip symlinks outside the corpus.
        if path.is_symlink():
            continue
        title = path.stem
        section = "Overview"
        body: list[str] = []
        sections: list[tuple[str, str]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            heading = HEADING.match(line)
            if heading is None:
                body.append(line)
                continue
            sections.append((section, "\n".join(body).strip()))
            body.clear()
            level, label = heading.groups()
            if level == "#" and title == path.stem:
                title = label
                section = "Overview"
            else:
                section = label
        sections.append((section, "\n".join(body).strip()))

        occurrences: Counter[str] = Counter()
        for section, text in sections:
            if not text:
                continue
            slug = re.sub(r"[^a-z0-9]+", "-", section.lower()).strip("-") or "section"
            words = text.split()
            for offset in range(0, len(words), MAX_CHUNK_WORDS):
                occurrences[slug] += 1
                suffix = occurrences[slug]
                chunks.append(RunbookChunk(
                    source_id=f"{path.name}#{slug}:{suffix}",
                    document=path.name,
                    title=title,
                    section=section,
                    text=text if len(words) <= MAX_CHUNK_WORDS else " ".join(
                        words[offset:offset + MAX_CHUNK_WORDS]
                    ),
                ))
    return tuple(chunks)


class Embedder(Protocol):
    def encode(self, sentences: list[str]) -> NDArray[np.float32]: ...


def unit_vectors(vectors: NDArray[np.float32]) -> NDArray[np.float32]:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return np.divide(vectors, norms, out=np.zeros_like(vectors), where=norms > 0)


class RunbookIndex:
    def __init__(self, chunks: tuple[RunbookChunk, ...], embedder: Embedder) -> None:
        self.chunks = chunks
        self.embedder = embedder
        documents = [f"{chunk.title}\n{chunk.section}\n{chunk.text}" for chunk in chunks]
        self.vectors = unit_vectors(embedder.encode(documents)) if documents else None

    def search(self, description: str, logs: str | None, top_k: int) -> list[RunbookChunk]:
        if self.vectors is None:
            return []
        queries = [description]
        if logs and logs.strip():
            queries.append(logs[:600])
            if len(logs) > 600:
                queries.append(logs[-600:])
        query_vectors = unit_vectors(self.embedder.encode(queries))
        scores = (self.vectors @ query_vectors.T).max(axis=1)
        ranked = np.argsort(-scores, kind="stable")
        relevant = [i for i in ranked if scores[i] >= MIN_SIMILARITY]
        return [self.chunks[i] for i in relevant[:top_k]]


@cache
def _load_index() -> RunbookIndex:
    from sentence_transformers import SentenceTransformer

    return RunbookIndex(
        load_chunks(RUNBOOK_DIR),
        SentenceTransformer(MODEL_ID, device="cpu", local_files_only=True, token=False),
    )


def retrieve(description: str, logs: str | None, top_k: int) -> list[RunbookChunk]:
    # Serialize first use and inference against the shared CPU model.
    with _INDEX_LOCK:
        return _load_index().search(description, logs, top_k)
