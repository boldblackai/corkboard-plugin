#!/usr/bin/env python3
"""Unit tests for Corkboard skill CLI pure logic.

Tests the pure functions from cb_pages.py that can be verified without
a live HTTP server: edit uniqueness, insert anchor resolution, and CAS
retry decision logic.

Usage: python3 tests/test_pages_logic.py
Exit 0 on pass, non-zero on failure.
"""

import os
import sys

# Ensure script/ is on the import path so we can import cb_pages
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, os.path.join(_PROJECT_ROOT, "script"))

from cb_client import CorkboardClient, CorkboardError

# ---------------------------------------------------------------------------
# Test framework (stdlib-only, no pytest)
# ---------------------------------------------------------------------------

_failures = 0
_checks = 0


def _check(condition, message):
    global _failures, _checks
    _checks += 1
    if not condition:
        _failures += 1
        print(f"FAIL: {message}")
        # Print a traceback-like line for debugging
        import traceback
        for line in traceback.format_stack()[:-1]:
            pass  # suppress full trace; we print the message
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


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

SAMPLE_BODY = """\
# Introduction

This is the intro section.

## Features

Feature one description.

Feature two description.

### Nested Feature

Nested content here.

## Installation

Run `pip install`.

# License

MIT license text.
"""

# ---------------------------------------------------------------------------
# apply_edits tests
# ---------------------------------------------------------------------------

def test_edit_single_replacement():
    """A single --old/--new pair that matches exactly once succeeds."""
    from cb_pages import apply_edits
    result = apply_edits(SAMPLE_BODY, [("intro section", "overview section")])
    _check("overview section" in result, "replacement text not found")
    _check("intro section" not in result, "original text still present")
    assert "\n" not in ("intro section", "overview section")  # check they're inline


def test_edit_zero_matches_aborts():
    """An --old that matches zero times should raise ValueError."""
    from cb_pages import apply_edits
    err = _raises(ValueError, apply_edits, SAMPLE_BODY, [("nonexistent text", "replacement")])
    _check(err is not None, "zero-match edit did not raise ValueError")
    _check("0 times" in str(err).lower() or "0 matches" in str(err).lower(),
           f"error message should mention zero matches, got: {err}")


def test_edit_multiple_matches_aborts():
    """An --old that matches more than once should raise ValueError."""
    from cb_pages import apply_edits
    # "##" appears multiple times in the body
    err = _raises(ValueError, apply_edits, SAMPLE_BODY, [("##", "##")])
    _check(err is not None, "multi-match edit did not raise ValueError")
    _check("times" in str(err).lower() and "2" not in str(err).lower() or "multiple" in str(err).lower(),
           f"error message should mention multiple matches, got: {err}")


def test_edit_sequential_application():
    """Later edits may anchor on the results of earlier edits."""
    from cb_pages import apply_edits
    body = "first line\nsecond line\nthird line\n"
    # First edit: replace "second line" with "SECOND LINE"
    # Second edit: replace "SECOND LINE" with "line two" (anchors on result of first)
    edits = [
        ("second line", "SECOND LINE"),
        ("SECOND LINE", "line two"),
    ]
    result = apply_edits(body, edits)
    _check("line two" in result, "second edit result not found")
    _check("second line" not in result, "original text still present")
    _check("SECOND LINE" not in result, "intermediate text still present")


def test_edit_sequential_fails_on_second():
    """If the second edit's --old doesn't match (because first edit changed it),
    the entire operation fails — no partial writes."""
    from cb_pages import apply_edits
    body = "first line\nsecond line\nthird line\n"
    edits = [
        ("second line", "SECOND LINE"),
        # This won't match because "second line" was already replaced
        ("second line", "never matches"),
    ]
    err = _raises(ValueError, apply_edits, body, edits)
    _check(err is not None, "sequential edit with bad second edit did not raise")


def test_edit_empty_edits_noop():
    """No edits: return original body unchanged."""
    from cb_pages import apply_edits
    result = apply_edits(SAMPLE_BODY, [])
    _check(result == SAMPLE_BODY, "empty edits should return body unchanged")


def test_edit_edits_file_format():
    """Edits from --edits FILE: one --old/--new pair per line, tab-separated."""
    from cb_pages import parse_edits_file
    import tempfile
    content = "old text\tnew text\nsecond old\tsecond new\n"
    pairs = parse_edits_file(content)
    _check(len(pairs) == 2, f"expected 2 pairs, got {len(pairs)}")
    _check(pairs[0] == ("old text", "new text"), f"first pair wrong: {pairs[0]}")
    _check(pairs[1] == ("second old", "second new"), f"second pair wrong: {pairs[1]}")

    # Empty file
    _check(parse_edits_file("") == [], "empty file should return empty list")

    # Trailing newline
    pairs = parse_edits_file("a\tb\n")
    _check(pairs == [("a", "b")], f"single line with trailing newline: {pairs}")


# ---------------------------------------------------------------------------
# resolve_anchor tests (insert)
# ---------------------------------------------------------------------------

def test_insert_under_heading():
    """Insert content under a heading — after the heading, before next same/higher level."""
    from cb_pages import resolve_anchor
    body = "# Heading 1\n\nContent under H1.\n\n## Subheading\n\nSub content.\n\n# Heading 2\n\nH2 content.\n"
    # Insert under "## Subheading" — should go before "# Heading 2" (same or higher level)
    result = resolve_anchor(body, "## Subheading", "under", "INSERTED\n")
    # Should be after "## Subheading" section and before "# Heading 2"
    _check("INSERTED" in result, "inserted text not found")
    # Verify position: INSERTED appears after Subheading content and before Heading 2
    idx_inserted = result.index("INSERTED")
    idx_sub = result.index("## Subheading")
    idx_h2 = result.index("# Heading 2")
    _check(idx_inserted > idx_sub, "INSERTED should be after Subheading")
    _check(idx_inserted < idx_h2, "INSERTED should be before Heading 2")


def test_insert_under_last_heading():
    """Insert under the last heading — goes to end of document."""
    from cb_pages import resolve_anchor
    body = "# H1\n\nContent.\n\n# H2\n\nLast content.\n"
    result = resolve_anchor(body, "# H2", "under", "APPENDED\n")
    _check(result.endswith("APPENDED\n"), "insert under last heading should go to end of doc")


def test_insert_after_line():
    """Insert after a specific line."""
    from cb_pages import resolve_anchor
    body = "line one\nline two\nline three\n"
    result = resolve_anchor(body, "line two", "after", "INSERTED\n")
    expected = "line one\nline two\nINSERTED\nline three\n"
    _check(result == expected, f"after insert:\n  expected: {repr(expected)}\n  got:      {repr(result)}")


def test_insert_before_line():
    """Insert before a specific line."""
    from cb_pages import resolve_anchor
    body = "line one\nline two\nline three\n"
    result = resolve_anchor(body, "line two", "before", "INSERTED\n")
    expected = "line one\nINSERTED\nline two\nline three\n"
    _check(result == expected, f"before insert:\n  expected: {repr(expected)}\n  got:      {repr(result)}")


def test_insert_anchor_not_found():
    """Anchor that doesn't match any line raises ValueError."""
    from cb_pages import resolve_anchor
    body = "line one\nline two\n"
    err = _raises(ValueError, resolve_anchor, body, "nonexistent", "after", "INSERTED\n")
    _check(err is not None, "missing anchor did not raise ValueError")


def test_insert_anchor_multiple_matches():
    """Anchor that matches multiple lines raises ValueError (exactly-one rule)."""
    from cb_pages import resolve_anchor
    body = "repeat\nunique\nrepeat\n"
    err = _raises(ValueError, resolve_anchor, body, "repeat", "after", "INSERTED\n")
    _check(err is not None, "multi-match anchor did not raise ValueError")


def test_insert_anchor_exact_line_match():
    """Anchor matches the full line, not a substring."""
    from cb_pages import resolve_anchor
    body = "prefix match here\nno match\n"
    # "match" is a substring of line 1, but the anchor is the full line
    err = _raises(ValueError, resolve_anchor, body, "match", "after", "INSERTED\n")
    _check(err is not None, "substring anchor matched — should require exact line match")


def test_insert_under_heading_levels():
    """Under a heading respects heading levels (Markdown ATX)."""
    from cb_pages import resolve_anchor
    body = "## H2\n\nH2 content.\n\n### H3\n\nH3 content.\n\n## H2b\n\nH2b content.\n"
    # Insert under "## H2" — section ends at next ## (same level) or # (higher)
    result = resolve_anchor(body, "## H2", "under", "INSERTED\n")
    idx_inserted = result.index("INSERTED")
    idx_h2b = result.index("## H2b")
    idx_h3 = result.index("### H3")
    _check(idx_inserted > idx_h3, "INSERTED should be after H3 section")
    _check(idx_inserted < idx_h2b, "INSERTED should be before next H2")


def test_insert_unknown_mode():
    """Unknown mode raises ValueError."""
    from cb_pages import resolve_anchor
    err = _raises(ValueError, resolve_anchor, "body", "anchor", "sideways", "INSERTED\n")
    _check(err is not None, "unknown mode did not raise ValueError")


# ---------------------------------------------------------------------------
# is_page_not_found_error tests (Bug A — 404 → create path)
# ---------------------------------------------------------------------------

def test_is_page_not_found_404():
    """CorkboardError with status 404 is a page-not-found."""
    from cb_pages import is_page_not_found_error
    from cb_client import CorkboardError
    err = CorkboardError("Not Found", status=404, body=b"")
    _check(is_page_not_found_error(err) is True, "404 should be page-not-found")


def test_is_page_not_found_other_status():
    """CorkboardError with status 500 is NOT a page-not-found."""
    from cb_pages import is_page_not_found_error
    from cb_client import CorkboardError
    for status in (400, 403, 500, 503):
        err = CorkboardError("Error", status=status, body=b"")
        _check(is_page_not_found_error(err) is False,
               f"status {status} should NOT be page-not-found")


def test_is_page_not_found_other_exception():
    """Non-CorkboardError exceptions are not page-not-found."""
    from cb_pages import is_page_not_found_error
    _check(is_page_not_found_error(ValueError("oops")) is False,
           "ValueError should not be page-not-found")
    _check(is_page_not_found_error(Exception("generic")) is False,
           "Exception should not be page-not-found")


# ---------------------------------------------------------------------------
# build_find_params tests (Bug D — pattern/flags → q/regex/ignore_case)
# ---------------------------------------------------------------------------

def test_build_find_params_literal():
    """Literal search sends q=pattern only."""
    from cb_pages import build_find_params
    params = build_find_params("hello")
    _check(params == {"q": "hello"},
           f"literal: expected {{'q': 'hello'}}, got {params}")


def test_build_find_params_regex():
    """Regex search adds regex=true."""
    from cb_pages import build_find_params
    params = build_find_params("[Bb]anana", extended=True)
    _check(params == {"q": "[Bb]anana", "regex": "true"},
           f"regex: expected regex=true, got {params}")


def test_build_find_params_ignore_case():
    """Case-insensitive search adds ignore_case=true."""
    from cb_pages import build_find_params
    params = build_find_params("apple", ignore_case=True)
    _check(params == {"q": "apple", "ignore_case": "true"},
           f"ignore_case: expected ignore_case=true, got {params}")


def test_build_find_params_both_flags():
    """Both -E and -i produce both flags."""
    from cb_pages import build_find_params
    params = build_find_params("pattern", extended=True, ignore_case=True)
    _check(params == {"q": "pattern", "regex": "true", "ignore_case": "true"},
           f"both flags: expected all three keys, got {params}")


def test_build_find_params_no_flags():
    """Neither flag produces only q."""
    from cb_pages import build_find_params
    params = build_find_params("test", extended=False, ignore_case=False)
    _check(params == {"q": "test"},
           f"no flags: expected only q, got {params}")


# ---------------------------------------------------------------------------
# build_body_payload tests (G6 — --sum wiring)
# ---------------------------------------------------------------------------

def test_build_body_payload_without_summary():
    """No summary → payload has body only."""
    from cb_client import build_body_payload
    payload = build_body_payload("hello")
    _check(payload == {"body": "hello"},
           f"expected body-only payload, got {payload}")


def test_build_body_payload_with_summary():
    """With summary → payload includes summary."""
    from cb_client import build_body_payload
    payload = build_body_payload("hello", "edit summary here")
    _check(payload == {"body": "hello", "summary": "edit summary here"},
           f"expected body+summary payload, got {payload}")


def test_build_body_payload_empty_summary_is_kept():
    """An explicitly empty summary is still sent (distinct from None)."""
    from cb_client import build_body_payload
    payload = build_body_payload("hello", "")
    _check(payload == {"body": "hello", "summary": ""},
           f"expected empty summary kept, got {payload}")


# ---------------------------------------------------------------------------
# CAS retry (put_cas mutate-callback) tests — G7
# ---------------------------------------------------------------------------

class _ScriptedClient(CorkboardClient):
    """CorkboardClient with scriptable get_page/request for CAS unit tests.

    ``_gets`` queues the page dicts returned by successive get_page calls.
    ``_put_results`` queues the result (dict or CorkboardError) returned by
    each PUT request.  ``puts`` records every (payload, headers) sent.
    """

    def __init__(self):
        super().__init__(base_url="http://test.local", token="test-token")
        self._gets = []
        self._put_results = []
        self.puts = []

    def get_page(self, page_id):
        return self._gets.pop(0)

    def request(self, method, path, data=None, headers=None, params=None,
                raw=False, unscoped=False):
        if method != "PUT":
            raise AssertionError(f"unexpected request {method} {path}")
        self.puts.append((data, headers))
        result = self._put_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


def test_cas_retry_derives_from_fresh_body():
    """On 412, the mutation is re-applied to the FRESH body, not the stale one."""
    from cb_pages import apply_edits

    client = _ScriptedClient()
    # edit-style mutation: append a marker to whatever body it receives.
    def mutate(fresh_body):
        return fresh_body + " [edited]"

    # Attempt 1 (callable): put_cas fetches the page, applies mutate, PUTs.
    client._gets.append({"body": "v1", "revision": 1})
    client._put_results.append(
        CorkboardError("Precondition failed", status=412, body=b"")
    )
    # Retry: re-fetch shows a concurrent writer's body (revision bumped).
    client._gets.append({"body": "v1 CONCURRENT", "revision": 2})
    client._put_results.append(
        {"body": "v1 CONCURRENT [edited]", "revision": 2}
    )

    result = client.put_cas("p", mutate)

    _check(len(client.puts) == 2, f"expected 2 PUTs, got {len(client.puts)}")
    # First PUT: mutation applied to the initially-fetched body.
    _check(client.puts[0][0] == {"body": "v1 [edited]"},
           f"first PUT payload wrong: {client.puts[0][0]}")
    _check(client.puts[0][1] == {"If-Match": '"1"'},
           f"first PUT headers wrong: {client.puts[0][1]}")
    # Retry PUT: mutation applied to the FRESH body — concurrent text preserved.
    _check(client.puts[1][0] == {"body": "v1 CONCURRENT [edited]"},
           f"retry PUT payload wrong (concurrent text lost?): {client.puts[1][0]}")
    _check(client.puts[1][1] == {"If-Match": '"2"'},
           f"retry PUT headers wrong: {client.puts[1][1]}")
    _check("CONCURRENT" in result["body"],
           "concurrent writer's text missing from final result")


def test_cas_conflict_reachable_on_second_412():
    """A second 412 raises the typed CONFLICT error (previously dead code)."""
    client = _ScriptedClient()
    # Replacement string: no get_page on attempt 1.
    client._put_results.append(
        CorkboardError("Precondition failed", status=412, body=b"")
    )
    # Retry re-fetches, then PUTs again.
    client._gets.append({"body": "v1b", "revision": 2})
    client._put_results.append(
        CorkboardError("Precondition failed", status=412, body=b"")
    )

    err = _raises(CorkboardError, client.put_cas, "p", "replacement body", revision=1)

    _check(err is not None, "second 412 did not raise CorkboardError")
    _check(err.status == 412, f"expected status 412, got {err.status}")
    _check("CONFLICT" in str(err), f"expected CONFLICT message, got: {err}")
    _check(len(client.puts) == 2, f"expected 2 PUTs, got {len(client.puts)}")
    _check(client.puts[0][1] == {"If-Match": '"1"'},
           f"first PUT headers wrong: {client.puts[0][1]}")
    _check(client.puts[1][1] == {"If-Match": '"2"'},
           f"retry PUT headers wrong: {client.puts[1][1]}")


def test_cas_replacement_resent_unchanged_on_retry():
    """For a replacement string, the 412 retry re-sends the SAME body."""
    client = _ScriptedClient()
    client._put_results.append(
        CorkboardError("Precondition failed", status=412, body=b"")
    )
    client._gets.append({"body": "concurrent", "revision": 2})
    client._put_results.append({"body": "replacement body", "revision": 2})

    result = client.put_cas("p", "replacement body", revision=1)

    _check(len(client.puts) == 2, f"expected 2 PUTs, got {len(client.puts)}")
    _check(client.puts[0][0] == {"body": "replacement body"},
           f"first PUT payload wrong: {client.puts[0][0]}")
    _check(client.puts[1][0] == {"body": "replacement body"},
           f"retry PUT should re-send same body: {client.puts[1][0]}")
    _check(result == {"body": "replacement body", "revision": 2},
           f"unexpected result: {result}")


def test_cas_mutation_summary_forwarded():
    """The summary is included on the initial PUT and the 412 retry."""
    client = _ScriptedClient()
    client._gets.append({"body": "v1", "revision": 1})
    client._put_results.append(
        CorkboardError("Precondition failed", status=412, body=b"")
    )
    client._gets.append({"body": "v1 concurrent", "revision": 2})
    client._put_results.append({"body": "v1 concurrent!", "revision": 2})

    client.put_cas("p", lambda fresh: fresh + "!", summary="my summary")

    _check(client.puts[0][0] == {"body": "v1!", "summary": "my summary"},
           f"first PUT payload wrong: {client.puts[0][0]}")
    _check(client.puts[1][0] == {"body": "v1 concurrent!", "summary": "my summary"},
           f"retry PUT payload wrong: {client.puts[1][0]}")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Run all test_ functions
    import traceback
    test_funcs = [(name, obj) for name, obj in sorted(globals().items())
                  if name.startswith("test_") and callable(obj)]

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