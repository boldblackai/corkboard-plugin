#!/usr/bin/env python3
"""Collections commands for the Corkboard CLI.

Talks to the Corkboard HTTP API v1.  Stdlib-only.
Each command is a plain function taking (client, args).  The register hook
wires them into an argparse subparser.
"""

from __future__ import annotations

import sys


# ---------------------------------------------------------------------------
# Exception for semantic-unavailable signalling
# ---------------------------------------------------------------------------

class SemanticUnavailable(Exception):
    """Raised when semantic search is unavailable (not a plan-gate)."""


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable)
# ---------------------------------------------------------------------------

def build_query_params(
    ns=None,
    depth=None,
    filter=None,   # noqa: A002  (shadowing builtin is intentional here)
    view=None,
    q=None,
):
    """Build a query-params dict, omitting None values."""
    params = {}
    if ns is not None:
        params["ns"] = ns
    if depth is not None:
        params["depth"] = depth
    if filter is not None:
        params["filter"] = filter
    if view is not None:
        params["view"] = view
    if q is not None:
        params["q"] = q
    return params


def build_sitemap_tree(pages):
    """Build a nested tree from a flat list of page IDs.

    Returns a dict::

        {
            "ns": "",
            "pages": [...],
            "children": [{"ns": "foo", "pages": [...], "children": [...]}, ...]
        }
    """
    root = {"ns": "", "pages": [], "children": []}
    ns_map: dict[str, dict] = {"": root}

    for page_id in sorted(pages):
        if ":" in page_id:
            ns_part, name = page_id.rsplit(":", 1)
        else:
            ns_part = ""
            name = page_id

        # Ensure every namespace segment exists in the tree
        segments = ns_part.split(":") if ns_part else []
        parent_ns = ""
        for i, seg in enumerate(segments):
            full_ns = ":".join(segments[: i + 1])
            if full_ns not in ns_map:
                node = {"ns": seg, "pages": [], "children": []}
                ns_map[parent_ns]["children"].append(node)
                ns_map[full_ns] = node
            parent_ns = full_ns

        node = ns_map[ns_part] if ns_part else root
        node["pages"].append(name)

    # Sort children of every node by ns
    _sort_tree(root)
    return root


def _sort_tree(node):
    """Sort pages and children recursively."""
    node["pages"].sort()
    node["children"].sort(key=lambda c: c["ns"])
    for child in node["children"]:
        _sort_tree(child)


def render_sitemap(tree, *, _prefix=""):
    """Render a sitemap tree as ASCII.

    Returns a multi-line string.
    """
    lines = []
    ns = tree.get("ns", "")
    pages = tree.get("pages", [])
    children = tree.get("children", [])

    if ns == "":
        # Root
        total = len(pages) + _count_subpages(children)
        label = f"(root) — {total} pages"
    else:
        total = len(pages) + _count_subpages(children)
        label = f"{ns}/ — {total} pages"

    lines.append(f"{_prefix}{label}")
    entries = list(pages) + [f"__NS__{c['ns']}" for c in children]
    for i, entry in enumerate(entries):
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        child_prefix = _prefix + ("    " if is_last else "│   ")

        if isinstance(entry, str) and entry.startswith("__NS__"):
            # Namespace child
            child_ns = entry[6:]
            child = next(c for c in children if c["ns"] == child_ns)
            lines.append(render_sitemap(child, _prefix=child_prefix))
        else:
            marker = " *" if entry == "start" and ns != "" else ""
            lines.append(f"{_prefix}{connector}{entry}{marker}")

    return "\n".join(lines)


def _count_subpages(children):
    """Count total pages across all children recursively."""
    total = 0
    for child in children:
        total += len(child.get("pages", []))
        total += _count_subpages(child.get("children", []))
    return total


def handle_semantic_error(status, body, *, connection_error=False):
    """Handle semantic-search error responses.

    - 402 (plan-gated) / 403 / 503 (embedding service down) → exit 0 with a
      user-friendly message (semantic search simply is not available).
    - ConnectionError → exit 1.
    - Any other error → raise SemanticUnavailable so the caller can decide.

    Returns normally on tolerant exits (the caller should not continue);
    raises SystemExit(0) / SystemExit(1) / SemanticUnavailable.
    """
    if connection_error:
        raise SystemExit(1)

    if status in (402, 403, 503):
        print(
            "semantic search unavailable (plan-gated or embedding service down)",
            file=sys.stderr,
        )
        raise SystemExit(0)

    raise SemanticUnavailable(
        f"semantic search failed: HTTP {status} — {_truncate_body(body)}"
    )


def _truncate_body(body, max_len=200):
    """Return a short string representation of the body for error messages."""
    if body is None:
        return "<no body>"
    if isinstance(body, bytes):
        s = body.decode("utf-8", errors="replace")
    else:
        s = str(body)
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


# ---------------------------------------------------------------------------
# CLI commands  (client, args) -> None
# ---------------------------------------------------------------------------

def _handle_http_error(err):
    """Print a uniform error message for HTTP errors."""
    print(f"error: HTTP {err.status} — {_truncate_body(getattr(err, 'body', None))}",
          file=sys.stderr)
    raise SystemExit(1)


def cmd_list(client, args):
    """List pages in a namespace."""
    params = build_query_params(ns=args.ns, depth=args.depth)
    try:
        data = client.get("pages", params=params)
    except Exception as e:
        if hasattr(e, "status"):
            _handle_http_error(e)
        raise
    # data is a list of page objects or a tree; print simple page IDs
    if isinstance(data, list):
        for page in data:
            print(page.get("id", page) if isinstance(page, dict) else page)
    else:
        # tree view — flatten for plain list
        _print_pages_flat(data)


def _print_pages_flat(node, prefix=""):
    """Recursively print page IDs from a tree node."""
    ns = node.get("ns", "")
    for page in node.get("pages", []):
        full_id = f"{ns}:{page}" if ns else page
        print(full_id)
    for child in node.get("children", []):
        _print_pages_flat(child)


def cmd_search(client, args):
    """Full-text search for pages."""
    params = build_query_params(q=args.query)
    if args.ns:
        params["ns"] = args.ns
    try:
        data = client.get("pages", params=params)
    except Exception as e:
        if hasattr(e, "status"):
            _handle_http_error(e)
        raise
    if isinstance(data, list):
        for page in data:
            print(page.get("id", page) if isinstance(page, dict) else page)
    else:
        print(str(data))


def cmd_sitemap(client, args):
    """Render an ASCII sitemap tree."""
    params = build_query_params(ns=args.ns, depth=args.depth)
    params["view"] = "tree"
    try:
        # Use the tree view; if the API returns a flat list, build the tree
        tree_data = client.get("pages", params=params)
    except Exception as e:
        if hasattr(e, "status"):
            _handle_http_error(e)
        raise

    if isinstance(tree_data, list):
        # API returned a flat list — build tree ourselves
        tree = build_sitemap_tree(
            [p.get("id", p) if isinstance(p, dict) else p for p in tree_data]
        )
    elif isinstance(tree_data, dict):
        # API returned a tree — render it directly
        tree = tree_data
    else:
        print("error: unexpected API response for sitemap", file=sys.stderr)
        raise SystemExit(1)

    print(render_sitemap(tree))


def cmd_wanted(client, args):
    """List pages with broken internal links."""
    try:
        data = client.get("pages", params={"filter": "wanted"})
    except Exception as e:
        if hasattr(e, "status"):
            _handle_http_error(e)
        raise
    if isinstance(data, list):
        for page in data:
            print(page.get("id", page) if isinstance(page, dict) else page)
    else:
        print(str(data))


def cmd_orphans(client, args):
    """List pages with no inbound links."""
    try:
        data = client.get("pages", params={"filter": "orphans"})
    except Exception as e:
        if hasattr(e, "status"):
            _handle_http_error(e)
        raise
    if isinstance(data, list):
        for page in data:
            print(page.get("id", page) if isinstance(page, dict) else page)
    else:
        print(str(data))


def cmd_semantic(client, args):
    """Semantic (vector) search."""
    try:
        data = client.get("pages/semantic", params={"q": args.query})
    except Exception as e:
        status = getattr(e, "status", None)
        body = getattr(e, "body", None)
        if status is not None:
            handle_semantic_error(status, body)
        # Check if it's a connection error
        if isinstance(e, OSError):
            handle_semantic_error(None, None, connection_error=True)
        raise

    if isinstance(data, list):
        for page in data:
            print(page.get("id", page) if isinstance(page, dict) else page)
    else:
        print(str(data))


# ---------------------------------------------------------------------------
# Argparse registration hook
# ---------------------------------------------------------------------------

def register_collections(subparsers, client_factory):
    """Register all collection sub-commands on the given subparsers object.

    ``client_factory`` is a callable that returns a client object.
    Each command receives ``(client, args)`` where ``client`` is the result
    of ``client_factory()``.
    """

    # list
    p_list = subparsers.add_parser("list", help="List pages in a namespace")
    p_list.add_argument("--ns", default=None, help="Namespace filter")
    p_list.add_argument("--depth", type=int, default=None,
                        help="Recursion depth (0 = full)")
    p_list.set_defaults(func=lambda args: cmd_list(client_factory(), args))

    # search
    p_search = subparsers.add_parser("search", help="Full-text search")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--ns", default=None, help="Namespace filter")
    p_search.set_defaults(func=lambda args: cmd_search(client_factory(), args))

    # sitemap
    p_sitemap = subparsers.add_parser("sitemap", help="Render ASCII sitemap tree")
    p_sitemap.add_argument("--ns", default=None, help="Namespace to expand")
    p_sitemap.add_argument("--depth", type=int, default=None,
                           help="Max depth")
    p_sitemap.set_defaults(func=lambda args: cmd_sitemap(client_factory(), args))

    # wanted
    p_wanted = subparsers.add_parser("wanted", help="List broken internal links")
    p_wanted.set_defaults(func=lambda args: cmd_wanted(client_factory(), args))

    # orphans
    p_orphans = subparsers.add_parser("orphans",
                                       help="List pages with no inbound links")
    p_orphans.set_defaults(func=lambda args: cmd_orphans(client_factory(), args))

    # semantic
    p_semantic = subparsers.add_parser("semantic",
                                        help="Semantic (vector) search")
    p_semantic.add_argument("query", help="Search query")
    p_semantic.set_defaults(func=lambda args: cmd_semantic(client_factory(), args))