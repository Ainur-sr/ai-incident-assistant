import sys
from types import SimpleNamespace

import numpy as np

from app import retrieval

from app.retrieval import MAX_CHUNK_WORDS, RUNBOOK_DIR, RunbookIndex, load_chunks


def test_ingests_all_synthetic_runbooks():
    chunks = load_chunks(RUNBOOK_DIR)

    assert len({chunk.document for chunk in chunks}) == 8
    assert len(chunks) == len({chunk.source_id for chunk in chunks}) == 16
    assert {chunk.section for chunk in chunks} == {"Signals", "Diagnostics"}
    assert all(chunk.text and chunk.title for chunk in chunks)
    assert chunks == load_chunks(RUNBOOK_DIR)


def test_chunks_long_sections_and_disambiguates_repeated_headings(tmp_path):
    path = tmp_path / "incident.md"
    long_section = " ".join(["word"] * (MAX_CHUNK_WORDS + 1))
    path.write_text(f"# Incident\n\n## Checks\n{long_section}\n## Checks\nAnother check.")
    (tmp_path / "ignore.txt").write_text("Ignored")
    (tmp_path / "symlink.md").symlink_to(path)

    chunks = load_chunks(tmp_path)

    assert [chunk.source_id for chunk in chunks] == [
        "incident.md#checks:1", "incident.md#checks:2", "incident.md#checks:3",
    ]
    assert [len(chunk.text.split()) for chunk in chunks] == [MAX_CHUNK_WORDS, 1, 2]
    assert all(chunk.title == "Incident" and chunk.section == "Checks" for chunk in chunks)


class FakeEmbedder:
    def __init__(self):
        self.calls = []

    def encode(self, sentences):
        self.calls.append(sentences)
        vectors = []
        for sentence in sentences:
            if "Kafka" in sentence:
                vector = [2, 0, 0]
            elif "High CPU" in sentence:
                vector = [10, 10, 0]
            else:
                vector = [0, 0, 1]
            vectors.append(vector)
        return np.asarray(vectors, dtype=np.float32)


def test_cosine_ranking_top_k_threshold_and_reuses_document_embeddings():
    embedder = FakeEmbedder()
    chunks = tuple(
        chunk for chunk in load_chunks(RUNBOOK_DIR)
        if chunk.document in {"kafka-consumer-lag.md", "high-cpu.md"}
    )
    index = RunbookIndex(chunks, embedder)

    results = index.search("Kafka partitions are falling behind", None, 3)
    assert [chunk.document for chunk in results] == [
        "kafka-consumer-lag.md", "kafka-consumer-lag.md", "high-cpu.md",
    ]
    assert not index.search("Unrelated symptom", None, 3)
    assert index.search("Unrelated symptom", "Kafka lag", 1) == results[:1]
    assert [len(batch) for batch in embedder.calls] == [4, 1, 1, 2]


def test_empty_index_does_not_embed():
    embedder = FakeEmbedder()
    index = RunbookIndex((), embedder)
    assert index.search("Network timeout", None, 3) == []
    assert embedder.calls == []



def test_retrieve_loads_the_local_encoder_and_corpus_once(monkeypatch):
    embedder = FakeEmbedder()
    loaded_models = []

    def load_model(model_id, **options):
        loaded_models.append((model_id, options))
        return embedder

    monkeypatch.setitem(sys.modules, "sentence_transformers", SimpleNamespace(
        SentenceTransformer=load_model
    ))
    retrieval._load_index.cache_clear()
    try:
        first = retrieval.retrieve("Kafka consumer lag", None, 1)
        second = retrieval.retrieve("Kafka consumer lag", None, 1)
        assert first == second
        assert first[0].document == "kafka-consumer-lag.md"
        assert loaded_models == [
            (retrieval.MODEL_ID, {"device": "cpu", "local_files_only": True, "token": False})
        ]
        assert [len(batch) for batch in embedder.calls] == [16, 1, 1]
    finally:
        retrieval._load_index.cache_clear()
