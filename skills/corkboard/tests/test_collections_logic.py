#!/usr/bin/env python3
"""Pure-logic unit tests for cb_collections module.

Tests cover:
  - sitemap tree rendering from a nested page-id list
  - query-param building (ns, depth, filter, view)
  - semantic-tolerance decision (403, 503, ConnectionError)

Run: python3 tests/test_collections_logic.py
Exit 0 = all pass; exit 1 = failures.
"""

import sys
import os

# Ensure the script/ dir is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'script'))

from cb_collections import (
    build_sitemap_tree,
    render_sitemap,
    build_query_params,
    SemanticUnavailable,
    handle_semantic_error,
)


# ---------------------------------------------------------------------------
# Test harness (no pytest)
# ---------------------------------------------------------------------------

_failures = 0
_ran = 0


def _check(description, condition):
    global _failures, _ran
    _ran += 1
    if not condition:
        _failures += 1
        print(f"FAIL: {description}")
    else:
        print(f"  ok: {description}")


def _finish():
    print(f"\n{_ran} tests, {_failures} failures")
    sys.exit(0 if _failures == 0 else 1)


# ---------------------------------------------------------------------------
# 1. build_query_params
# ---------------------------------------------------------------------------

def test_build_query_params():
    # No args
    _check("empty params", build_query_params() == {})

    # Single params
    _check("ns only", build_query_params(ns="projects") == {"ns": "projects"})
    _check("depth only", build_query_params(depth=2) == {"depth": 2})
    _check("filter only", build_query_params(filter="wanted") == {"filter": "wanted"})
    _check("view only", build_query_params(view="tree") == {"view": "tree"})

    # Combined
    params = build_query_params(ns="projects", depth=3, filter="orphans")
    _check("combined ns/depth/filter",
           params == {"ns": "projects", "depth": 3, "filter": "orphans"})

    # Search query (q=)
    params = build_query_params(q="foo bar")
    _check("search query", params == {"q": "foo bar"})

    # None values are omitted
    _check("none values omitted",
           build_query_params(ns=None, depth=None, filter=None, view=None) == {})

    # Zero depth is valid (recursive)
    params = build_query_params(depth=0)
    _check("depth zero", params == {"depth": 0})


# ---------------------------------------------------------------------------
# 2. build_sitemap_tree + render_sitemap
# ---------------------------------------------------------------------------

def test_build_sitemap_tree_empty():
    tree = build_sitemap_tree([])
    _check("empty tree is dict", isinstance(tree, dict))
    _check("empty tree has no pages", tree.get("pages", []) == [])


def test_build_sitemap_tree_root_only():
    pages = ["start", "getting-started", "faq"]
    tree = build_sitemap_tree(pages)
    _check("root pages count", len(tree["pages"]) == 3)
    _check("root pages sorted", tree["pages"] == ["faq", "getting-started", "start"])
    _check("no children", tree.get("children", []) == [])


def test_build_sitemap_tree_one_ns():
    pages = ["start", "projects:start", "projects:alpha", "projects:beta"]
    tree = build_sitemap_tree(pages)
    _check("root has start", "start" in tree["pages"])
    _check("one namespace child", len(tree["children"]) == 1)
    child = tree["children"][0]
    _check("child ns name", child["ns"] == "projects")
    _check("child pages sorted",
           child["pages"] == ["alpha", "beta", "start"])


def test_build_sitemap_tree_nested_ns():
    pages = [
        "start",
        "projects:start",
        "projects:alpha",
        "projects:sub:deep",
        "projects:sub:start",
        "journal:2024-q1",
    ]
    tree = build_sitemap_tree(pages)
    _check("root has start", "start" in tree["pages"])
    _check("two children", len(tree["children"]) == 2)

    # Find projects child
    proj = [c for c in tree["children"] if c["ns"] == "projects"][0]
    _check("projects has pages", "alpha" in proj["pages"])
    _check("projects has start", "start" in proj["pages"])
    _check("projects has sub-ns", len(proj["children"]) == 1)

    sub = proj["children"][0]
    _check("sub ns name", sub["ns"] == "sub")
    _check("sub pages", "deep" in sub["pages"])

    # Find journal child
    jrn = [c for c in tree["children"] if c["ns"] == "journal"][0]
    _check("journal has page", "2024-q1" in jrn["pages"])


def test_render_sitemap_root_only():
    tree = {"ns": "", "pages": ["start", "faq"]}
    out = render_sitemap(tree)
    _check("root label", "(root) — 2 pages" in out)
    _check("start page", "start" in out)
    _check("faq page", "faq" in out)


def test_render_sitemap_with_ns():
    tree = {
        "ns": "",
        "pages": ["start"],
        "children": [
            {"ns": "projects", "pages": ["start", "alpha"], "children": []},
            {"ns": "journal", "pages": ["2024-q1"], "children": []},
        ]
    }
    out = render_sitemap(tree)
    _check("root label present", "(root) — 4 pages" in out)
    _check("projects ns", "projects/ — 2 pages" in out)
    _check("journal ns", "journal/ — 1 pages" in out)
    _check("projects alpha", "alpha" in out)
    _check("journal page", "2024-q1" in out)


def test_render_sitemap_nested():
    tree = {
        "ns": "",
        "pages": ["start"],
        "children": [
            {
                "ns": "projects",
                "pages": ["start"],
                "children": [
                    {"ns": "sub", "pages": ["deep"], "children": []}
                ]
            }
        ]
    }
    out = render_sitemap(tree)
    _check("nested sub ns", "sub/ — 1 pages" in out)
    _check("deep page", "deep" in out)


def test_render_sitemap_mark_start():
    tree = {
        "ns": "",
        "pages": ["start"],
        "children": [
            {"ns": "projects", "pages": ["start", "alpha"], "children": []},
        ]
    }
    out = render_sitemap(tree)
    # The projects:start should be marked with * since it's a namespace start page
    _check("start page marked", "start *" in out)


def test_render_sitemap_empty():
    tree = {"ns": "", "pages": [], "children": []}
    out = render_sitemap(tree)
    _check("empty root", "(root) — 0 pages" in out)


# ---------------------------------------------------------------------------
# 3. Semantic tolerance
# ---------------------------------------------------------------------------

def test_handle_semantic_403():
    try:
        handle_semantic_error(403, '{"error":"plan required"}')
        _check("403 does not raise", True)
    except SystemExit as e:
        _check("403 exits with code 0", e.code == 0)


def test_handle_semantic_402():
    # Plan gate (Free org) returns 402 — must be tolerated the same way.
    try:
        handle_semantic_error(402, '{"error":"Semantic search requires a Pro plan or higher."}')
        _check("402 does not raise", True)
    except SystemExit as e:
        _check("402 exits with code 0", e.code == 0)


def test_handle_semantic_503():
    try:
        handle_semantic_error(503, "Service Unavailable")
        _check("503 does not raise", True)
    except SystemExit as e:
        _check("503 exits with code 0", e.code == 0)


def test_handle_semantic_connection_error():
    # Simulate what happens when a ConnectionError is caught upstream
    # The function should raise SystemExit(1)
    raised = False
    try:
        handle_semantic_error(None, None, connection_error=True)
    except SystemExit as e:
        raised = True
        _check("ConnectionError exits with code 1", e.code == 1)
    if not raised:
        _check("ConnectionError raises SystemExit", False)


def test_handle_semantic_other_http_error():
    # A 500 should raise SemanticUnavailable (which the caller can then handle)
    try:
        handle_semantic_error(500, "Internal Error")
        _check("500 should raise, but didn't", False)
    except SemanticUnavailable:
        _check("500 raises SemanticUnavailable", True)
    except SystemExit:
        _check("500 raises SemanticUnavailable, not SystemExit", False)


def test_handle_semantic_401():
    # 401 is an auth error, not a plan-gate — should propagate
    try:
        handle_semantic_error(401, "Unauthorized")
        _check("401 should raise, but didn't", False)
    except SemanticUnavailable:
        _check("401 raises SemanticUnavailable", True)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    test_build_query_params()
    test_build_sitemap_tree_empty()
    test_build_sitemap_tree_root_only()
    test_build_sitemap_tree_one_ns()
    test_build_sitemap_tree_nested_ns()
    test_render_sitemap_root_only()
    test_render_sitemap_with_ns()
    test_render_sitemap_nested()
    test_render_sitemap_mark_start()
    test_render_sitemap_empty()
    test_handle_semantic_403()
    test_handle_semantic_402()
    test_handle_semantic_503()
    test_handle_semantic_connection_error()
    test_handle_semantic_other_http_error()
    test_handle_semantic_401()
    _finish()