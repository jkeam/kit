"""Tests for concurrent-safe session persistence in gateway/session_manager.py:
save_message locks the session file and writes atomically, so concurrent
writers can't corrupt or clobber each other's history."""

import threading

import pytest

from gateway.session_manager import SessionManager
from runtime.agents import AgentRegistry


class _NoEmbeddings:
    def __init__(self, *args, **kwargs):
        raise RuntimeError("embeddings disabled for tests")


@pytest.fixture(autouse=True)
def _no_real_embeddings(monkeypatch):
    monkeypatch.setattr("gateway.session_manager.EmbeddingsManager", _NoEmbeddings)


@pytest.fixture
def session_manager(tmp_path):
    registry = AgentRegistry(workspace_dir=str(tmp_path / "workspace"))
    return SessionManager(agent_registry=registry)


def test_concurrent_save_message_does_not_lose_writes(session_manager):
    session_id = "web:concurrency-test"
    n_threads = 8
    n_per_thread = 10

    def writer(i):
        for j in range(n_per_thread):
            session_manager.save_message(session_id, "user", f"thread-{i}-msg-{j}")

    threads = [threading.Thread(target=writer, args=(i,)) for i in range(n_threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    messages = session_manager.get_messages(session_id)
    assert len(messages) == n_threads * n_per_thread

    seen = {m["content"] for m in messages}
    expected = {f"thread-{i}-msg-{j}" for i in range(n_threads) for j in range(n_per_thread)}
    assert seen == expected


def test_session_file_is_never_left_partially_written(session_manager):
    session_id = "web:atomic-test"
    session_manager.save_message(session_id, "user", "hello")

    path = session_manager._session_file(session_id)
    # A readable, complete JSON file should exist - not a .tmp leftover.
    assert path.exists()
    assert not list(path.parent.glob("*.tmp.*"))
