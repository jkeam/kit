"""Tests for the opt-in GATEWAY_TOKEN auth gate in gateway/server.py.

Exercises _require_gateway_token / _websocket_auth directly rather than
booting the app through TestClient, since app startup (lifespan) loads the
real embedding model — unnecessary weight for testing an env-var-gated
auth check.
"""

import base64

import pytest
from fastapi import HTTPException

from gateway.server import _require_gateway_token, _websocket_auth


def _subprotocol_for_token(token: str) -> str:
    encoded = base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")
    return f"kit-token.{encoded}"


class FakeWebSocket:
    def __init__(self, token=None, subprotocols=None):
        self.query_params = {"token": token} if token is not None else {}
        self.scope = {"subprotocols": subprotocols or []}


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
    assert _websocket_auth(FakeWebSocket()) == (True, None)


def test_websocket_rejects_missing_auth(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_auth(FakeWebSocket()) == (False, None)


def test_websocket_rejects_wrong_query_param(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_auth(FakeWebSocket(token="wrong")) == (False, None)


def test_websocket_accepts_correct_query_param(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    assert _websocket_auth(FakeWebSocket(token="secret123")) == (True, None)


def test_websocket_rejects_wrong_subprotocol_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    ws = FakeWebSocket(subprotocols=[_subprotocol_for_token("wrong")])
    assert _websocket_auth(ws) == (False, None)


def test_websocket_accepts_correct_subprotocol_token(monkeypatch):
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    subprotocol = _subprotocol_for_token("secret123")
    ws = FakeWebSocket(subprotocols=[subprotocol])
    assert _websocket_auth(ws) == (True, subprotocol)


def test_websocket_subprotocol_preferred_over_query_param(monkeypatch):
    # Even if a (wrong) query param is also present, a valid subprotocol
    # token should still authenticate.
    monkeypatch.setenv("GATEWAY_TOKEN", "secret123")
    subprotocol = _subprotocol_for_token("secret123")
    ws = FakeWebSocket(token="wrong", subprotocols=[subprotocol])
    assert _websocket_auth(ws) == (True, subprotocol)
