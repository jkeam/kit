"""Regression test for runtime/embeddings.py: re-indexing a file that has
shrunk (edited, or a daily log cleaned up) must not leave orphaned stale
chunks behind in the vector store - those were still surfaced by
memory_search even after the source content was deleted from disk.
"""

import pytest

from runtime.embeddings import EmbeddingsManager


@pytest.fixture
def embeddings(tmp_path):
    return EmbeddingsManager(workspace_dir=str(tmp_path), collection_name="test-memories")


def test_reindexing_a_shrunk_file_removes_orphaned_chunks(embeddings, tmp_path):
    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    log_file = memory_dir / "2026-01-01.md"

    # Many distinct paragraphs -> many chunks.
    log_file.write_text("\n\n".join(f"Paragraph {i} about topic {i}." for i in range(20)))
    embeddings.index_file(log_file, "daily_log")
    count_before = embeddings.collection.count()
    assert count_before > 1

    # File shrinks drastically (e.g. stale/fabricated entries cleaned up).
    log_file.write_text("Just one short paragraph left.")
    # Force re-indexing regardless of the in-memory hash cache (a fresh
    # process, e.g. after a restart, would naturally re-index every file).
    embeddings.indexed_files.pop(str(log_file), None)
    embeddings.index_file(log_file, "daily_log")

    count_after = embeddings.collection.count()
    assert count_after < count_before

    results = embeddings.collection.get(where={"source": str(log_file)})
    assert len(results["ids"]) == count_after
    assert all("Just one short paragraph left" in doc for doc in results["documents"])
