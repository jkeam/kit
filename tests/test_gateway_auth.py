"""Tests for the opt-in GATEWAY_TOKEN auth gate in gateway/server.py.

Exercises _require_gateway_token / _websocket_token_valid directly rather
than booting the app through TestClient, since app startup (lifespan) loads
the real embedding model — unnecessary weight for testing an env-var-gated
auth check.
"""

import pytest
from fastapi import HTTPException

from gateway.server import _require_gateway_token, _websocket_token_valid


class FakeWebSocket:
    def __init__(self, token=None):
        self.query_params = {"token": token} if token is not None else {}


def test_http_open_when_token_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    # No exception, no Authorization header needed.
    _require_gateway_token(authorization=None)


def test_http_rejects_missing_header_when_token_set(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        _require_gateway_token(authorization=None)
    assert exc_info.value.status_code == 401


def test_http_rejects_malformed_header(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        _require_gateway_token(authorization="secret123")  # missing "Bearer " prefix
    assert exc_info.value.status_code == 401


def test_http_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    with pytest.raises(HTTPException) as exc_info:
        _require_gateway_token(authorization="Bearer wrong-token")
    assert exc_info.value.status_code == 401


def test_http_accepts_correct_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    # No exception raised.
    _require_gateway_token(authorization="Bearer secret123")


def test_websocket_open_when_token_unset(monkeypatch):
    monkeypatch.delenv("GATEWAY_TOKEN", raising=False)
    assert _websocket_token_valid(FakeWebSocket()) is True


def test_websocket_rejects_missing_query_param(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_token_valid(FakeWebSocket()) is False


def test_websocket_rejects_wrong_query_param(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_token_valid(FakeWebSocket(token="wrong")) is False


def test_websocket_accepts_correct_query_param(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_token_valid(FakeWebSocket(token="secret123")) is True
