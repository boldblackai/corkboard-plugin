#!/usr/bin/env python3
"""Smoke matrix — drives script/corkboard.py against the mock server.

Each test asserts exit codes and/or output patterns against the mock
server.  Commands affected by known upstream bugs (S1/S2 slices) are
flagged as SKIP rather than failures.

Usage:
    python3 tests/smoke_matrix.py [--port PORT]

Environment set by this script:
    CORKBOARD_URL=http://127.0.0.1:PORT
    CORKBOARD_TOKEN=test-token
    CORKBOARD_WORKSPACE=acme/main  (rows can unset or override it)

The mock models the workspace-scoped grammar: content lives under
/api/v1/o/{org}/{ws}/…, discovery at the unscoped /api/v1/me.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import argparse

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_failures = 0
_ran = 0
_skipped = 0

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

MOCK_URL = ""
ENTRYPOINT = ""


def _run(cmd, expected_exit=0, expect_contains=None,
         skip_reason=None, workspace="acme/main", token="test-token",
         unset_workspace=False):
    """Run a CLI command and assert exit code / output patterns.

    ``workspace`` sets CORKBOARD_WORKSPACE (default ``acme/main``);
    ``unset_workspace=True`` runs without it so the client falls back to
    the me-derived default.  If skip_reason is set, the test is recorded
    as skipped regardless of outcome (used for commands broken by
    upstream S1/S2 bugs).
    """
    global _ran, _failures, _skipped

    if skip_reason:
        _ran += 1
        _skipped += 1
        print(f"{YELLOW} SKIP [{_ran}]{RESET} {' '.join(cmd)}  — {skip_reason}")
        return True

    _ran += 1

    env = os.environ.copy()
    env["CORKBOARD_URL"] = MOCK_URL
    env["CORKBOARD_TOKEN"] = token
    env.pop("CORKBOARD_WORKSPACE", None)
    if workspace is not None and not unset_workspace:
        env["CORKBOARD_WORKSPACE"] = workspace

    full_cmd = ["python3", ENTRYPOINT] + cmd
    result = subprocess.run(full_cmd, capture_output=True, text=True, env=env, timeout=15)
    combined = result.stdout + result.stderr

    ok = True
    if result.returncode != expected_exit:
        print(f"{RED}FAIL [{_ran}]{RESET} {' '.join(cmd)}")
        print(f"  expected exit {expected_exit}, got {result.returncode}")
        if result.stdout:
            print(f"  stdout: {result.stdout[:200]}")
        if result.stderr:
            print(f"  stderr: {result.stderr[:300]}")
        ok = False
    elif expect_contains is not None and expect_contains not in combined:
        print(f"{RED}FAIL [{_ran}]{RESET} {' '.join(cmd)}")
        print(f"  expected output to contain: {expect_contains!r}")
        print(f"  combined: {combined[:300]}")
        ok = False
    else:
        print(f"{GREEN}  ok [{_ran}]{RESET} {' '.join(cmd)}")

    if not ok:
        _failures += 1
    return ok


def _run_expect_fail(cmd, expect_contains=None, **kwargs):
    """Run a command expected to fail (non-zero exit)."""
    _run(cmd, expected_exit=1, expect_contains=expect_contains, **kwargs)


def _run_media_get_roundtrip(media_id, out_path, expected_bytes):
    """Run media-get and assert the downloaded bytes match the uploaded bytes."""
    global _ran, _failures

    _ran += 1

    env = os.environ.copy()
    env["CORKBOARD_URL"] = MOCK_URL
    env["CORKBOARD_TOKEN"] = "test-token"
    env["CORKBOARD_WORKSPACE"] = "acme/main"

    full_cmd = ["python3", ENTRYPOINT, "media-get", media_id, "-o", out_path]
    result = subprocess.run(full_cmd, capture_output=True, text=True, env=env, timeout=15)
    combined = result.stdout + result.stderr

    ok = result.returncode == 0
    if ok:
        try:
            with open(out_path, "rb") as f:
                got = f.read()
            if got != expected_bytes:
                ok = False
                print(f"{RED}FAIL [{_ran}]{RESET} {' '.join(full_cmd)}")
                print(f"  byte round-trip mismatch: expected {len(expected_bytes)} bytes, got {len(got)}")
        except OSError as e:
            ok = False
            print(f"{RED}FAIL [{_ran}]{RESET} {' '.join(full_cmd)}")
            print(f"  could not read output file: {e}")
    else:
        print(f"{RED}FAIL [{_ran}]{RESET} {' '.join(full_cmd)}")
        print(f"  expected exit 0, got {result.returncode}")
        print(f"  combined: {combined[:300]}")

    if ok:
        print(f"{GREEN}  ok [{_ran}]{RESET} media-get {media_id} (byte round-trip)")
    else:
        _failures += 1
    return ok


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    global _ran, _failures, _skipped, MOCK_URL, ENTRYPOINT

    parser = argparse.ArgumentParser(description="Smoke matrix for Corkboard CLI")
    parser.add_argument("--port", type=int, default=18080, help="Mock server port (default: 18080)")
    parser.add_argument("--entrypoint", default="script/corkboard.py",
                        help="Path to CLI entrypoint (default: script/corkboard.py)")
    args = parser.parse_args()

    MOCK_URL = f"http://127.0.0.1:{args.port}"
    ENTRYPOINT = args.entrypoint

    print(f"Smoke matrix against {MOCK_URL}")
    print(f"Entrypoint: {ENTRYPOINT}")
    print()

    # ==================================================================
    # Discovery — me + workspace resolution
    # ==================================================================

    print("--- Discovery (me) ---")

    _run(["me"], expect_contains="acme/main")
    _run(["me"], expect_contains="Test User")
    _run(["me", "--json"], expect_contains='"workspaces"')

    # me-derived default: no workspace env and no --workspace flag
    _run(["get", "start"], expect_contains='"id": "start"', unset_workspace=True)

    # explicit --workspace beats the CORKBOARD_WORKSPACE env value
    _run(["--workspace", "beta/docs", "get", "start"],
         expect_contains='"id": "start"', workspace="acme/main")

    # explicit --workspace the token cannot reach → 404 (SEC-108: an
    # inaccessible pair is indistinguishable from an unknown one)
    _run_expect_fail(["--workspace", "nope/nope", "get", "start"],
                     expect_contains="404")

    # token with no accessible workspaces → actionable error
    _run_expect_fail(["get", "start"], expect_contains="no accessible workspace",
                     token="test-token-empty", unset_workspace=True)

    # ==================================================================
    # Page commands — S1 module (cb_pages.py)
    # ==================================================================

    print("--- Pages (S1: cb_pages.py) ---")

    _run(["get", "start"], expect_contains='"id": "start"')
    _run(["get", "sandbox"], expect_contains='"id": "sandbox"')
    _run_expect_fail(["get", "nonexistent"], expect_contains="404")

    _run(["put", "start", "--text", "# Updated\n\nNew body.\n", "--sum", "update start"],
         expect_contains='"id": "start"')

    _run(["append", "start", "--text", "\n\nAppended section.", "--sum", "append"],
         expect_contains='"id": "start"')

    _run(["edit", "start", "--old", "Updated", "--new", "Refreshed", "--sum", "edit"],
         expect_contains='"id": "start"')

    fd, edits_file = tempfile.mkstemp(suffix=".tsv")
    with os.fdopen(fd, "w") as f:
        f.write("Refreshed\tWelcome Back\n")
    _run(["edit", "start", "--edits", edits_file, "--sum", "edit via file"],
         expect_contains='"id": "start"')
    os.unlink(edits_file)

    _run(["insert", "start", "--under", "# Welcome Back", "--text", "Under heading.",
          "--sum", "insert under"],
         expect_contains='"id": "start"')

    _run(["insert", "start", "--after", "# Welcome Back", "--text", "After heading.",
          "--sum", "insert after"],
         expect_contains='"id": "start"')

    _run(["insert", "start", "--before", "# Welcome Back", "--text", "Before heading.",
          "--sum", "insert before"],
         expect_contains='"id": "start"')

    _run(["find", "start", "Welcome"], expect_contains='"count"')

    _run(["move", "sandbox", "playground"],
         expect_contains='"id": "playground"')

    _run(["links", "start"], expect_contains="sandbox")
    _run(["backlinks", "start"], expect_contains="start")
    _run(["revisions", "start"], expect_contains='"revisions"')
    _run(["revision-show", "start", "1"], expect_contains='"revision"')

    _run(["delete", "playground"],
         expect_contains='"deleted"')

    # CAS conflict rows (mock forces 412s via sentinel page ids)
    _run(["put", "cas-retry-success", "--text", "line one\nline two\n", "--sum", "create"],
         expect_contains='"id": "cas-retry-success"')
    _run(["edit", "cas-retry-success", "--old", "line one", "--new", "line ONE", "--sum", "cas edit"],
         expect_contains="CONCURRENT-ADDITION")

    _run(["put", "cas-double-conflict", "--text", "hello\n", "--sum", "create"],
         expect_contains='"id": "cas-double-conflict"')
    _run_expect_fail(["edit", "cas-double-conflict", "--old", "hello", "--new", "HELLO"],
                      expect_contains="CONFLICT")

    # ==================================================================
    # Collections — S2 module (cb_collections.py)
    # All commands with params pass kwargs to client.get() which only
    # accepts (path, params=None).  Commands without params work.
    # ==================================================================

    print("\n--- Collections (S2: cb_collections.py) ---")

    _run(["list"], expect_contains="start")

    _run(["list", "--ns", "root"])
    _run(["search", "Welcome"], expect_contains="start")
    _run(["sitemap"], expect_contains="start")

    _run(["wanted"], expect_contains="missing-page")
    _run(["orphans"], expect_contains="sandbox")
    _run(["semantic", "test"], expect_contains="semantic search unavailable")

    # ==================================================================
    # Media — S2 module (cb_media.py)
    # upload unpacks client.request() as (status, body) but request()
    # returns parsed JSON, not a tuple.  Downstream commands cascade.
    # ==================================================================

    print("\n--- Media (S2: cb_media.py) ---")

    # Upload a file with known binary content to prove byte round-trip.
    roundtrip_bytes = b"corkboard media round-trip\x00\x01\x02\xff\n"
    fd, upload_path = tempfile.mkstemp(suffix=".bin")
    with os.fdopen(fd, "wb") as f:
        f.write(roundtrip_bytes)

    _run(["media-upload", upload_path, "test", "hello.txt"],
         expect_contains="test/hello.txt")
    _run(["media-list"])
    _run(["media-list", "--ns", "test"], expect_contains="test/hello.txt")

    out_dir = tempfile.mkdtemp()
    out_path = os.path.join(out_dir, "roundtrip.out")
    _run_media_get_roundtrip("test/hello.txt", out_path, roundtrip_bytes)

    _run(["media-usage", "test/hello.txt"], expect_contains="start")
    _run(["media-move", "test/hello.txt", "test/renamed.txt"],
         expect_contains="moved")
    _run(["media-orphans"])
    _run(["media-delete", "test/renamed.txt"], expect_contains="deleted")

    os.unlink(upload_path)

    # ==================================================================
    # Summary
    # ==================================================================

    print(f"\n{_ran} commands: {_ran - _failures - _skipped} passed, "
          f"{_skipped} skipped (known upstream bugs), {_failures} failures")
    if _failures == 0:
        print(f"{GREEN}ALL SMOKE TESTS PASSED{RESET}")
        sys.exit(0)
    else:
        print(f"{RED}{_failures} SMOKE TEST(S) FAILED{RESET}")
        sys.exit(1)


if __name__ == "__main__":
    main()