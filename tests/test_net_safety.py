"""Tests for the SSRF guard in tools/net_safety.py."""

import pytest

from tools.net_safety import UnsafeURLError, assert_url_is_safe


def test_blocks_loopback():
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("http://127.0.0.1/")


def test_blocks_localhost_hostname():
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("http://localhost/")


def test_blocks_cloud_metadata_ip():
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("http://169.254.169.254/latest/meta-data/")


def test_blocks_private_network_ip():
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("http://10.0.0.5/")
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("http://192.168.1.1/")


def test_blocks_non_http_scheme():
    with pytest.raises(UnsafeURLError):
        assert_url_is_safe("file:///etc/passwd")


def test_allows_public_ip_literal():
    # 93.184.216.34 (example.com) is a public address - should pass the
    # address check without needing a live network call.
    assert_url_is_safe("http://93.184.216.34/")
