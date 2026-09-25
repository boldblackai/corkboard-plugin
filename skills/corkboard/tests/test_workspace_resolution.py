#!/usr/bin/env python3
"""Workspace resolution + URL grammar tests for the Corkboard client.

Covers the issue-#2 contract:

  * url building — content paths → ``/api/v1/o/{org}/{ws}/…``; ``me`` unscoped
  * resolution order — explicit arg > CORKBOARD_WORKSPACE > me-derived
  * me-derived default resolved once (cached per client) + empty-pairs error
  * mock models the real 21b7552 grammar: old unscoped paths return 404

A real HTTP server (the mock) runs in-process on an ephemeral port; no
external network is used.

Usage: python3 tests/test_workspace_resolution.py
Exit 0 on pass, non-zero on failure.
"""

import contextlib
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "script"))
sys.path.insert(0, _THIS_DIR)

from cb_client import (  # noqa: E402
    CorkboardClient,
    CorkboardError,
    parse_workspace_spec,
    workspace_pairs_from_me,
)
from mock_server import MockHandler  # noqa: E402

ENTRYPOINT = os.path.join(_PROJECT_ROOT, "script", "corkboard.py")

_failures = 0
_checks = 0

# ---------------------------------------------------------------------------
# Test framework (stdlib-only, no pytest)
# ---------------------------------------------------------------------------


def _check(condition, message):
    global _failures, _checks
    _checks += 1
    if not condition:
        _failures += 1
        print(f"FAIL: {message}")
        return False
    return True


def _raises(exc_type, fn, *args, **kwargs):
    try:
        fn(*args, **kwargs)
        _check(False, f"expected {exc_type.__name__} but no exception raised")
        return None
    except exc_type as e:
        return e
    except Exception as e:
        _check(False, f"expected {exc_type.__name__} but got {type(e).__name__}: {e}")
        return None


# ---------------------------------------------------------------------------
# In-process mock server
# ---------------------------------------------------------------------------

_server = None
BASE_URL = ""


def _ensure_server():
    global _server, BASE_URL
    if _server is None:
        _server = ThreadingHTTPServer(("127.0.0.1", 0), MockHandler)
        BASE_URL = f"http://127.0.0.1:{_server.server_address[1]}"
        thread = threading.Thread(target=_server.serve_forever, daemon=True)
        thread.start()
    return BASE_URL


@contextlib.contextmanager
def _workspace_env(value):
    """Temporarily set (or clear) CORKBOARD_WORKSPACE."""
    saved = os.environ.get("CORKBOARD_WORKSPACE")
    if value is None:
        os.environ.pop("CORKBOARD_WORKSPACE", None)
    else:
        os.environ["CORKBOARD_WORKSPACE"] = value
    try:
        yield
    finally:
        if saved is None:
            os.environ.pop("CORKBOARD_WORKSPACE", None)
        else:
            os.environ["CORKBOARD_WORKSPACE"] = saved


def _client(token="test-token", workspace=None):
    return CorkboardClient(base_url=_ensure_server(), token=token, workspace=workspace)


def _raw_get(path, token="test-token"):
    """Raw urllib GET; returns (status, body). Raises HTTPError on >=400."""
    req = urllib.request.Request(
        f"{_ensure_server()}{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        return resp.status, resp.read()


# ---------------------------------------------------------------------------
# URL grammar
# ---------------------------------------------------------------------------

def test_scoped_url_shape():
    """Content paths build /api/v1/o/{org}/{ws}/{path}."""
    client = _client(workspace="acme/main")
    url = client._build_url("pages/start")
    _check(url == f"{BASE_URL}/api/v1/o/acme/main/pages/start",
           f"scoped url wrong: {url}")

    url = client._build_url("/pages/start")  # leading slash tolerated
    _check(url == f"{BASE_URL}/api/v1/o/acme/main/pages/start",
           f"leading-slash url wrong: {url}")

    url = client._build_url("pages", params={"ns": "projects", "depth": 2})
    _check(url == f"{BASE_URL}/api/v1/o/acme/main/pages?ns=projects&depth=2",
           f"params url wrong: {url}")


def test_me_url_stays_unscoped():
    """me builds /api/v1/me with no workspace segment."""
    client = _client(workspace="acme/main")
    url = client._build_url("me", unscoped=True)
    _check(url == f"{BASE_URL}/api/v1/me", f"me url wrong: {url}")
    _check("/o/" not in url, f"me url must not be workspace-scoped: {url}")


def test_me_endpoint_reachable_without_workspace():
    """me() works with no workspace set — it is the discovery call."""
    with _workspace_env(None):
        payload = _client().me()
    _check(isinstance(payload, dict), f"me payload not a dict: {payload!r}")
    _check(isinstance(payload.get("workspaces"), list), "me payload lacks workspaces list")
    _check(payload["workspaces"][0]["org"]["slug"] == "acme",
           f"first org slug wrong: {payload['workspaces'][0]!r}")
    _check(payload["workspaces"][0]["ws"]["slug"] == "main",
           f"first ws slug wrong: {payload['workspaces'][0]!r}")


def test_me_payload_plan_is_an_object():
    """Shape pin: ``plan`` is ``{name, trial_days_remaining}`` (PlanContext).

    Not a bare plan-name string.  With no accessible workspace there is no
    primary organization, so ``plan`` is null — same as
    ``MeController``'s ``$plan?->toResponseArray()``.
    """
    with _workspace_env(None):
        payload = _client().me()
    plan = payload.get("plan")
    _check(isinstance(plan, dict), f"plan should be an object, got {plan!r}")
    if isinstance(plan, dict):
        _check(plan.get("name") == "team", f"plan name wrong: {plan!r}")
        _check("trial_days_remaining" in plan,
               f"plan lacks trial_days_remaining: {plan!r}")

    empty = _client(token="test-token-empty").me()
    _check(empty.get("plan") is None,
           f"plan should be null with no workspace, got {empty.get('plan')!r}")


def test_slash_page_ids_resolve_under_workspace_scope():
    """Greedy ``{id}`` routes: ``ns/page`` ids are legal on live.

    Live declares ``->where('id', '.*')`` on every page/media id route, so
    a namespaced id spans the slash.  Both the show route and its
    sub-routes must keep the full id.
    """
    client = _client(workspace="acme/main")
    client.put("pages/ns/page", data={"body": "slash id body"})

    page = client.get_page("ns/page")
    _check(page.get("id") == "ns/page", f"slash id round-trip failed: {page!r}")

    status, _ = _raw_get("/api/v1/o/acme/main/pages/ns/page")
    _check(status == 200, f"raw slash-id GET returned {status}, expected 200")

    links = client.get("pages/ns/page/links")
    _check(links.get("page") == "ns/page",
           f"slash-id sub-route lost the id: {links!r}")


# ---------------------------------------------------------------------------
# Resolution order: explicit arg > env > me-derived
# ---------------------------------------------------------------------------

def test_explicit_arg_beats_env():
    with _workspace_env("beta/docs"):
        client = _client(workspace="acme/main")
        _check(client.workspace == ("acme", "main"),
               f"explicit arg should win, got {client.workspace}")
        _check("/o/acme/main/" in client._build_url("pages/start"),
               "explicit arg not used in url")


def test_env_used_when_no_arg():
    with _workspace_env("beta/docs"):
        client = _client()
        _check(client.workspace == ("beta", "docs"),
               f"env workspace should win, got {client.workspace}")
        _check("/o/beta/docs/" in client._build_url("pages/start"),
               "env workspace not used in url")


def test_me_derived_default_is_first_accessible_pair():
    with _workspace_env(None):
        client = _client()
        _check(client.workspace == ("acme", "main"),
               f"me-derived default should be first pair, got {client.workspace}")
        page = client.get_page("start")
        _check(page.get("id") == "start",
               f"me-derived scoped fetch failed: {page!r}")


def test_me_derived_resolved_once_and_cached():
    with _workspace_env(None):
        client = _client()
        MockHandler.me_calls = 0
        client.get_page("start")
        client.get_page("sandbox")
        _check(MockHandler.me_calls == 1,
               f"me should be called once per client, got {MockHandler.me_calls}")
        _check(client.workspace == ("acme", "main"), "workspace not cached")


def test_no_accessible_workspace_raises():
    with _workspace_env(None):
        client = _client(token="test-token-empty")
        err = _raises(CorkboardError, client.get_page, "start")
        _check(err is not None, "empty workspaces did not raise CorkboardError")
        if err is not None:
            _check("no accessible workspace" in str(err),
                   f"error should say no accessible workspace, got: {err}")


def test_explicit_pair_the_token_cannot_reach_is_not_found():
    """An inaccessible pair answers 404, not 403 (SEC-108 no-existence-leak).

    403 is reserved for scope-denied writes (``api.scope:*``); the
    workspace resolver makes an unknown and an inaccessible pair
    indistinguishable.
    """
    client = _client(workspace="nope/nope")
    err = _raises(CorkboardError, client.get_page, "start")
    _check(err is not None, "inaccessible workspace did not raise")
    if err is not None:
        _check(err.status == 404, f"expected 404, got {err.status}")


# ---------------------------------------------------------------------------
# Spec parsing / payload parsing helpers
# ---------------------------------------------------------------------------

def test_parse_workspace_spec():
    _check(parse_workspace_spec("acme/main") == ("acme", "main"), "basic spec")
    _check(parse_workspace_spec("/acme/main/") == ("acme", "main"),
           "leading/trailing slashes tolerated")
    _check(parse_workspace_spec(None) is None, "None spec → None")
    _check(parse_workspace_spec("") is None, "empty spec → None")

    for bad in ("acme", "acme/main/extra", "/", "acme//"):
        err = _raises(CorkboardError, parse_workspace_spec, bad)
        _check(err is not None, f"spec {bad!r} should be rejected")
        if err is not None:
            _check("org_slug/ws_slug" in str(err),
                   f"error should name the expected form, got: {err}")


def test_workspace_pairs_from_me_tolerates_partial_payloads():
    _check(workspace_pairs_from_me({}) == [], "missing workspaces → []")
    _check(workspace_pairs_from_me({"workspaces": None}) == [],
           "null workspaces → []")
    _check(workspace_pairs_from_me({"workspaces": [
        "junk",
        {"org": {"slug": "a"}, "ws": {"slug": "b"}},
        {"org": {"slug": "x"}, "ws": {"slug": None}},
    ]}) == [("a", "b")], "malformed entries skipped")


# ---------------------------------------------------------------------------
# Old grammar is dead
# ---------------------------------------------------------------------------

def test_old_unscoped_content_paths_404():
    for old_path in ("/api/v1/pages/start", "/api/v1/pages", "/api/v1/pages/semantic"):
        try:
            status, _ = _raw_get(old_path)
            _check(False, f"old path {old_path} returned {status}, expected 404")
        except urllib.error.HTTPError as e:
            _check(e.code == 404, f"old path {old_path} returned {e.code}, expected 404")


def test_unscoped_me_requires_auth():
    try:
        status, _ = _raw_get("/api/v1/me", token="")
        _check(False, f"unauthenticated me returned {status}, expected 401")
    except urllib.error.HTTPError as e:
        _check(e.code == 401, f"unauthenticated me returned {e.code}, expected 401")


# ---------------------------------------------------------------------------
# CLI surface
# ---------------------------------------------------------------------------

def _run_cli(args, token="test-token", workspace=None):
    env = os.environ.copy()
    env["CORKBOARD_URL"] = _ensure_server()
    env["CORKBOARD_TOKEN"] = token
    env.pop("CORKBOARD_WORKSPACE", None)
    if workspace is not None:
        env["CORKBOARD_WORKSPACE"] = workspace
    return subprocess.run(
        ["python3", ENTRYPOINT] + args,
        capture_output=True, text=True, env=env, cwd=_PROJECT_ROOT, timeout=30,
    )


def test_cli_me_command_lists_identity_and_pairs():
    result = _run_cli(["me"], workspace="beta/docs")
    out = result.stdout + result.stderr
    _check(result.returncode == 0, f"me exited {result.returncode}: {out[:200]}")
    _check("acme/main" in out, f"me output missing first pair: {out[:300]}")
    _check("beta/docs" in out, f"me output missing second pair: {out[:300]}")
    _check("Test User" in out, f"me output missing identity: {out[:300]}")


def test_cli_me_json_flag_prints_raw_payload():
    result = _run_cli(["me", "--json"])
    _check(result.returncode == 0, f"me --json exited {result.returncode}")
    try:
        payload = json.loads(result.stdout)
    except ValueError as e:
        _check(False, f"me --json output not JSON: {e}")
        return
    _check(payload["workspaces"][0]["ws"]["slug"] == "main",
           f"raw payload wrong: {payload!r}")


def test_cli_workspace_flag_beats_env_and_me_default():
    result = _run_cli(["--workspace", "beta/docs", "get", "start"], workspace="acme/main")
    _check(result.returncode == 0, f"explicit --workspace failed: {result.stderr[:200]}")
    _check('"id": "start"' in result.stdout, "explicit --workspace fetch failed")

    result = _run_cli(["get", "start"])
    _check(result.returncode == 0,
           f"me-derived default via CLI failed: {result.stderr[:200]}")

    result = _run_cli(["get", "start"], token="test-token-empty")
    _check(result.returncode == 1, f"expected exit 1, got {result.returncode}")
    _check("no accessible workspace" in (result.stdout + result.stderr),
           f"missing actionable error: {result.stderr[:200]}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import traceback

    test_funcs = [(name, obj) for name, obj in sorted(globals().items())
                  if name.startswith("test_") and callable(obj)]

    try:
        _ensure_server()
        for name, func in test_funcs:
            try:
                func()
            except Exception as e:
                _failures += 1
                print(f"ERROR in {name}: {e}")
                traceback.print_exc()
    finally:
        if _server is not None:
            _server.shutdown()
            _server.server_close()

    print(f"\n{_checks} checks, {_failures} failures")
    if _failures == 0:
        print("ALL TESTS PASSED")
        sys.exit(0)
    print(f"{_failures} TEST(S) FAILED")
    sys.exit(1)
