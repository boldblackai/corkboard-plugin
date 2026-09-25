#!/usr/bin/env python3
"""Tests for CorkboardClient configuration behavior.

Tests cover:
  - Default base_url when env var is absent (CORKBOARD_URL optional)
  - Env override still honored
  - Missing token error still raised
  - Explicit base_url passed to constructor

Run: python3 tests/test_client_config.py
"""

import os
import sys

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "script"))

from cb_client import CorkboardClient, CorkboardError

_failures = 0
_checks = 0


def _check(condition, message):
    global _failures, _checks
    _checks += 1
    if not condition:
        _failures += 1
        print(f"FAIL: {message}")
        return False
    return True


def _raises(exc_type, fn, *args, **kwargs):
    """Assert that fn(*args, **kwargs) raises exc_type."""
    try:
        fn(*args, **kwargs)
        _check(False, f"expected {exc_type.__name__} but no exception raised")
        return None
    except exc_type as e:
        return e
    except Exception as e:
        _check(False, f"expected {exc_type.__name__} but got {type(e).__name__}: {e}")
        return None


def test_default_base_url_when_env_absent():
    """When CORKBOARD_URL is not set, base_url defaults to https://corkboard.wiki."""
    # Remove CORKBOARD_URL from env for this test
    saved = os.environ.pop("CORKBOARD_URL", None)
    saved_token = os.environ.get("CORKBOARD_TOKEN")
    os.environ["CORKBOARD_TOKEN"] = "cb_test_token"
    try:
        client = CorkboardClient()
        _check(
            client.base_url == "https://corkboard.wiki",
            f"expected base_url 'https://corkboard.wiki', got '{client.base_url}'",
        )
    finally:
        if saved is not None:
            os.environ["CORKBOARD_URL"] = saved
        if saved_token is not None:
            os.environ["CORKBOARD_TOKEN"] = saved_token
        else:
            os.environ.pop("CORKBOARD_TOKEN", None)


def test_env_override_still_honored():
    """When CORKBOARD_URL is set, the env value is used (not the default)."""
    saved = os.environ.get("CORKBOARD_URL")
    saved_token = os.environ.get("CORKBOARD_TOKEN")
    os.environ["CORKBOARD_URL"] = "https://custom.example.com"
    os.environ["CORKBOARD_TOKEN"] = "cb_test_token"
    try:
        client = CorkboardClient()
        _check(
            client.base_url == "https://custom.example.com",
            f"expected base_url 'https://custom.example.com', got '{client.base_url}'",
        )
    finally:
        if saved is not None:
            os.environ["CORKBOARD_URL"] = saved
        else:
            os.environ.pop("CORKBOARD_URL", None)
        if saved_token is not None:
            os.environ["CORKBOARD_TOKEN"] = saved_token
        else:
            os.environ.pop("CORKBOARD_TOKEN", None)


def test_explicit_base_url_overrides_default():
    """Explicit base_url argument overrides both env and default."""
    saved_url = os.environ.get("CORKBOARD_URL")
    saved_token = os.environ.get("CORKBOARD_TOKEN")
    os.environ.pop("CORKBOARD_URL", None)
    os.environ["CORKBOARD_TOKEN"] = "cb_test_token"
    try:
        client = CorkboardClient(base_url="http://127.0.0.1:8013")
        _check(
            client.base_url == "http://127.0.0.1:8013",
            f"expected base_url 'http://127.0.0.1:8013', got '{client.base_url}'",
        )
    finally:
        if saved_url is not None:
            os.environ["CORKBOARD_URL"] = saved_url
        if saved_token is not None:
            os.environ["CORKBOARD_TOKEN"] = saved_token
        else:
            os.environ.pop("CORKBOARD_TOKEN", None)


def test_missing_token_still_errors():
    """Missing CORKBOARD_TOKEN still raises CorkboardError."""
    saved_url = os.environ.get("CORKBOARD_URL")
    saved_token = os.environ.pop("CORKBOARD_TOKEN", None)
    os.environ.pop("CORKBOARD_URL", None)
    try:
        err = _raises(CorkboardError, CorkboardClient)
        _check(err is not None, "missing token did not raise CorkboardError")
        _check(
            "CORKBOARD_TOKEN" in str(err),
            f"error should mention CORKBOARD_TOKEN, got: {err}",
        )
    finally:
        if saved_url is not None:
            os.environ["CORKBOARD_URL"] = saved_url
        if saved_token is not None:
            os.environ["CORKBOARD_TOKEN"] = saved_token


def test_base_url_trailing_slash_stripped():
    """Trailing slash on URL is stripped by __init__."""
    saved_url = os.environ.get("CORKBOARD_URL")
    saved_token = os.environ.get("CORKBOARD_TOKEN")
    os.environ["CORKBOARD_URL"] = "https://corkboard.wiki/"
    os.environ["CORKBOARD_TOKEN"] = "cb_test_token"
    try:
        client = CorkboardClient()
        _check(
            client.base_url == "https://corkboard.wiki",
            f"expected trailing slash stripped, got '{client.base_url}'",
        )
    finally:
        if saved_url is not None:
            os.environ["CORKBOARD_URL"] = saved_url
        else:
            os.environ.pop("CORKBOARD_URL", None)
        if saved_token is not None:
            os.environ["CORKBOARD_TOKEN"] = saved_token
        else:
            os.environ.pop("CORKBOARD_TOKEN", None)


if __name__ == "__main__":
    import traceback

    test_funcs = [
        (name, obj)
        for name, obj in sorted(globals().items())
        if name.startswith("test_") and callable(obj)
    ]

    for name, func in test_funcs:
        try:
            func()
        except Exception as e:
            _failures += 1
            print(f"ERROR in {name}: {e}")
            traceback.print_exc()

    print(f"\n{_checks} checks, {_failures} failures")
    if _failures == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        print(f"{_failures} TEST(S) FAILED")
        sys.exit(1)