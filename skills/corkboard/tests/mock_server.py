#!/usr/bin/env python3
"""Corkboard API v1 mock server — stdlib http.server.

Models the server grammar live since corkboard-app 21b7552:

* ``GET /api/v1/me`` — unscoped discovery: identity + accessible
  ``workspaces: [{org: {slug…}, ws: {slug…}}]`` pairs.
* content endpoints — workspace-scoped only:
  ``/api/v1/o/{org}/{ws}/…``
* OLD unscoped content paths (``/api/v1/pages/…``) → 404.

Includes a 412 CAS conflict once-then-success scenario and a 403 on
semantic search.

An org/ws pair the token cannot reach answers 404, not 403 (SEC-108
no-existence-leak): on live, ```ResolveWorkspace``` returns
``{"error": "Workspace not found."}`` with status 404, and 403 is
reserved for scope-denied writes (``api.scope:*``).

Tokens are user-scoped: one token reaches every accessible workspace.
The mock's token ``test-token`` sees ``acme/main`` and ``beta/docs``;
any token starting with ``empty`` sees none (for the no-accessible-
workspace error path).

Usage:
    python3 tests/mock_server.py [--port PORT] [--host HOST]
    Default: http://127.0.0.1:18080
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_pages: dict[str, dict] = {}
_media: dict[str, bytes] = {}

# Pre-seed a page for tests
_pages["start"] = {
    "id": "start",
    "body": "# Welcome\n\nThis is the start page.\n\n## Getting Started\n\nSet up your workspace.\n",
    "revision": 1,
    "body_revision": 1,
    "title": "Welcome",
}
_pages["sandbox"] = {
    "id": "sandbox",
    "body": "# Sandbox\n\nA page for testing.\n\nEdit me.\n",
    "revision": 1,
    "body_revision": 1,
    "title": "Sandbox",
}

_service_root = {"service": "corkboard", "version": "0.1.0", "api_version": "v1", "status": "ok"}

# ---------------------------------------------------------------------------
# Token → accessible workspaces (user-scoped PAT model)
# ---------------------------------------------------------------------------

ACCESSIBLE_WORKSPACES = [("acme", "main"), ("beta", "docs")]


def accessible_workspaces(token):
    """Return the [(org_slug, ws_slug)] pairs this token can reach.

    A token containing ``empty`` reaches nothing — used to exercise the
    no-accessible-workspace error path.
    """
    if "empty" in (token or ""):
        return []
    return list(ACCESSIBLE_WORKSPACES)


def me_payload(token):
    """Build the /api/v1/me response body for a token."""
    workspaces = []
    for index, (org_slug, ws_slug) in enumerate(accessible_workspaces(token), start=1):
        workspaces.append({
            "org": {"id": index, "slug": org_slug, "name": org_slug.title()},
            "ws": {"id": index, "slug": ws_slug, "name": ws_slug.title()},
        })
    first = workspaces[0] if workspaces else {}
    # Live shape (PlanContext::toResponseArray): an object, not a bare
    # string.  No primary organization (no accessible workspace) → null,
    # matching MeController's `$plan?->toResponseArray()`.
    plan = (
        {"name": "team", "trial_days_remaining": None} if workspaces else None
    )
    return {
        "user": {"id": 1, "name": "Test User", "email": "test@example.com"},
        "organization": first.get("org"),
        "workspace": first.get("ws"),
        "plan": plan,
        "workspaces": workspaces,
    }


# ---------------------------------------------------------------------------
# CAS conflict forcing — deterministic 412s for smoke-matrix testing.
#
# Each entry maps page_id -> {remaining: N, inject: bool}.
# When a PUT with If-Match arrives for a page in this map and remaining
# > 0, the mock sends a 412 and decrements remaining.  After delivering
# the last forced 412, If-Match checks work normally.
#
# If ``inject`` is True, the mock also inserts a concurrent-writer
# marker into the page body *after* the first 412, simulating a
# real-world CAS conflict where another writer edits the page between
# the client's fetch and its retry.
# ---------------------------------------------------------------------------

_CAS_CONFLICT_SPECS: dict[str, dict] = {}
# Sentinel page IDs auto-registered with their conflict spec:
_CAS_SENTINELS = {
    "cas-retry-success":   {"remaining": 1, "inject": True},
    "cas-double-conflict": {"remaining": 2, "inject": False},
}
for _pid, _spec in _CAS_SENTINELS.items():
    _CAS_CONFLICT_SPECS[_pid] = dict(_spec)  # mutable copy per instance

_CONCURRENT_MARKER = "CONCURRENT-ADDITION\n"


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class MockHandler(BaseHTTPRequestHandler):
    """Handle Corkboard API v1 requests (workspace-scoped grammar)."""

    # Number of /api/v1/me calls served — used by the caching tests.
    me_calls = 0

    def log_message(self, fmt, *args):
        pass

    def _send_json(self, data, status=200):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_text(self, text, status=200, content_type="text/plain"):
        body = text.encode("utf-8") if isinstance(text, str) else text
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status, message):
        self._send_json({"error": message}, status=status)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return b""
        return self.rfile.read(length)

    def _get_json_body(self):
        raw = self._read_body()
        if not raw:
            return {}
        return json.loads(raw.decode("utf-8"))

    def _get_token(self):
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return auth[7:]
        return ""

    def _check_auth(self):
        token = self._get_token()
        if not token:
            self._send_error(401, "Unauthorized")
            return False
        return True

    # ------------------------------------------------------------------
    # Path parsing
    # ------------------------------------------------------------------

    def _route(self):
        """Parse the request path.

        Returns ``(scope, org, ws, rest, query)`` where scope is one of:

        * ``root``    — ``/api/v1`` (service root, unscoped)
        * ``me``      — ``/api/v1/me`` (unscoped discovery)
        * ``ws``      — ``/api/v1/o/{org}/{ws}/…`` (org + ws + rest filled)
        * ``invalid`` — anything else, incl. the OLD unscoped content paths
        """
        parsed = urlparse(self.path)
        p = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if p == "/api/v1":
            return ("root", None, None, "/", qs)
        if p == "/api/v1/me":
            return ("me", None, None, "/me", qs)
        m = re.match(r"^/api/v1/o/([^/]+)/([^/]+)(.*)$", p)
        if m:
            rest = m.group(3) or "/"
            if not rest.startswith("/"):
                rest = "/" + rest
            return ("ws", m.group(1), m.group(2), rest, qs)
        return ("invalid", None, None, p, qs)

    def _dispatch(self, method):
        scope, org, ws, rest, qs = self._route()

        if scope == "root":
            return self._send_json(_service_root)
        if scope == "invalid":
            # OLD unscoped content paths land here → 404 (dead grammar).
            return self._send_error(404, "Not found")
        if not self._check_auth():
            return
        if scope == "me":
            if method != "GET":
                return self._send_error(405, "Method not allowed")
            return self._handle_me()

        # Workspace scope: the token must reach this org/ws pair.
        # Live answers 404 here (SEC-108 no-existence-leak) — an unknown and
        # an inaccessible pair are indistinguishable to the caller.
        if (org, ws) not in accessible_workspaces(self._get_token()):
            return self._send_error(404, "Workspace not found.")
        return self._ws_dispatch(method, rest, qs)

    # ------------------------------------------------------------------
    # HTTP verbs
    # ------------------------------------------------------------------

    def do_GET(self):
        self._dispatch("GET")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_POST(self):
        self._dispatch("POST")

    def do_DELETE(self):
        self._dispatch("DELETE")

    # ------------------------------------------------------------------
    # Workspace-scoped routing (rest of the path after /o/{org}/{ws})
    # ------------------------------------------------------------------

    def _ws_dispatch(self, method, p, qs):
        if p == "/":
            return self._send_json(_service_root)

        if method == "GET":
            # Media sub-routes first (they share /media prefix with pages)
            m = re.match(r"^/media/(.*)/usage$", p)
            if m:
                return self._handle_media_usage(m.group(1))
            m = re.match(r"^/media/(.*)$", p)
            if m:
                return self._handle_media_get(m.group(1))
            if p == "/media":
                return self._handle_media_list(qs)

            # Semantic search
            if p == "/pages/semantic":
                return self._handle_semantic(qs)

            # Pages collection
            if p == "/pages":
                return self._handle_pages_list(qs)

            # Individual page sub-routes.  Page ids are greedy on live
            # (`->where('id', '.*')`), so ``ns/page`` ids are legal: the
            # sub-routes must be matched before the show route, and the id
            # group must span slashes.
            m = re.match(r"^/pages/(.*)/links$", p)
            if m:
                return self._handle_links(m.group(1))
            m = re.match(r"^/pages/(.*)/backlinks$", p)
            if m:
                return self._handle_backlinks(m.group(1))
            m = re.match(r"^/pages/(.*)/revisions/([^/]+)$", p)
            if m:
                return self._handle_revision_show(m.group(1), m.group(2))
            m = re.match(r"^/pages/(.*)/revisions$", p)
            if m:
                return self._handle_revisions(m.group(1))
            m = re.match(r"^/pages/(.*)/find$", p)
            if m:
                return self._handle_find(m.group(1), qs)
            m = re.match(r"^/pages/(.*)$", p)
            if m:
                return self._handle_get_page(m.group(1))

        elif method == "PUT":
            m = re.match(r"^/media/(.*)$", p)
            if m:
                return self._handle_media_put(m.group(1))

            m = re.match(r"^/pages/(.*)$", p)
            if m:
                return self._handle_put_page(m.group(1))

        elif method == "POST":
            m = re.match(r"^/pages/(.*)/append$", p)
            if m:
                return self._handle_append(m.group(1))
            m = re.match(r"^/pages/(.*)/move$", p)
            if m:
                return self._handle_move(m.group(1))
            m = re.match(r"^/media/(.*)/move$", p)
            if m:
                return self._handle_media_move(m.group(1))

        elif method == "DELETE":
            m = re.match(r"^/pages/(.*)$", p)
            if m:
                return self._handle_delete_page(m.group(1))
            m = re.match(r"^/media/(.*)$", p)
            if m:
                return self._handle_media_delete(m.group(1))

        self._send_error(404, "Not found")

    # ------------------------------------------------------------------
    # Identity / discovery
    # ------------------------------------------------------------------

    def _handle_me(self):
        MockHandler.me_calls += 1
        return self._send_json(me_payload(self._get_token()))

    # ------------------------------------------------------------------
    # Page handlers
    # ------------------------------------------------------------------

    def _handle_get_page(self, page_id):
        page = _pages.get(page_id)
        if page is None:
            return self._send_error(404, "Page not found")
        return self._send_json(page)

    def _handle_put_page(self, page_id):
        data = self._get_json_body()
        if_match = self.headers.get("If-Match", "").strip('"')

        existing = _pages.get(page_id)
        current_rev = existing["revision"] if existing else 0

        # ── CAS conflict forcing (deterministic 412s for smoke tests) ──
        if if_match and existing:
            spec = _CAS_CONFLICT_SPECS.get(page_id)
            if spec and spec["remaining"] > 0:
                spec["remaining"] -= 1
                if spec.get("inject") and spec["remaining"] == 0:
                    # After the last forced 412, pretend a concurrent
                    # writer modified the page so the CLI's re-fetch
                    # sees unexpected text.
                    existing["body"] = _CONCURRENT_MARKER + existing["body"]
                    existing["revision"] += 1
                    existing["body_revision"] += 1
                return self._send_error(412, "Precondition Failed: page modified")
        # ── end CAS conflict forcing ──

        if if_match:
            try:
                client_rev = int(if_match)
            except ValueError:
                client_rev = -1
            # 412 once, then succeed on retry (simulates bounded CAS retry)
            if client_rev != current_rev and client_rev != 9999:
                return self._send_error(412, "Precondition Failed: page modified")

        body = data.get("body", "")
        rev = current_rev + 1
        _pages[page_id] = {
            "id": page_id,
            "body": body,
            "revision": rev,
            "body_revision": rev,
            "title": page_id.split("/")[-1],
        }
        return self._send_json(_pages[page_id])

    def _handle_append(self, page_id):
        data = self._get_json_body()
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        body = data.get("body", "")
        _pages[page_id]["body"] += body
        _pages[page_id]["revision"] += 1
        _pages[page_id]["body_revision"] = _pages[page_id]["revision"]
        return self._send_json(_pages[page_id])

    def _handle_delete_page(self, page_id):
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        del _pages[page_id]
        return self._send_json({"deleted": True, "id": page_id})

    def _handle_move(self, page_id):
        data = self._get_json_body()
        dst = data.get("to", "")
        if page_id not in _pages:
            return self._send_error(404, "Source page not found")
        page = _pages.pop(page_id)
        page["id"] = dst
        page["revision"] += 1
        page["body_revision"] = page["revision"]
        _pages[dst] = page
        return self._send_json(page)

    def _handle_find(self, page_id, qs):
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        pattern = qs.get("q", [""])[0]
        body = _pages[page_id]["body"]
        matches = []
        for i, line in enumerate(body.split("\n"), 1):
            if pattern in line:
                matches.append({"line": i, "text": line.strip()})
        return self._send_json({"matches": matches, "count": len(matches)})

    def _handle_links(self, page_id):
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        return self._send_json({"links": ["sandbox"], "page": page_id})

    def _handle_backlinks(self, page_id):
        return self._send_json({"backlinks": ["start"], "page": page_id})

    def _handle_revisions(self, page_id):
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        current = _pages[page_id]
        return self._send_json({
            "revisions": [
                {"revision": current["revision"], "timestamp": "2026-01-01T00:00:00Z"},
            ]
        })

    def _handle_revision_show(self, page_id, rev):
        if page_id not in _pages:
            return self._send_error(404, "Page not found")
        page = _pages[page_id]
        return self._send_json({
            "id": page_id,
            "body": page["body"],
            "revision": int(rev),
        })

    # ------------------------------------------------------------------
    # Collections / list / search
    # ------------------------------------------------------------------

    def _handle_pages_list(self, qs):
        ns = qs.get("ns", [None])[0]
        filter_val = qs.get("filter", [None])[0]
        view = qs.get("view", [None])[0]
        query = qs.get("q", [None])[0]

        pages = list(_pages.values())

        if ns:
            pages = [p for p in pages if p["id"].startswith(ns + "/")]

        if filter_val == "wanted":
            pages = [{"id": "missing-page", "wanted": True}]
        elif filter_val == "orphans":
            pages = [{"id": "sandbox", "orphan": True}]

        if query:
            pages = [p for p in pages if query.lower() in p.get("body", "").lower()]

        if view == "tree":
            tree = {"ns": "", "pages": [], "children": []}
            root_pages = [p["id"] for p in pages if "/" not in p["id"]]
            ns_pages: dict[str, list] = {}
            for p in pages:
                pid = p["id"] if isinstance(p, dict) else p
                if "/" in pid:
                    nsp, name = pid.rsplit("/", 1)
                    ns_pages.setdefault(nsp, []).append(name)
            tree["pages"] = sorted(root_pages)
            for nsp in sorted(ns_pages):
                tree["children"].append({"ns": nsp, "pages": sorted(ns_pages[nsp]), "children": []})
            return self._send_json(tree)

        result = [{"id": p["id"], "title": p.get("title", "")} for p in pages]
        return self._send_json(result)

    # ------------------------------------------------------------------
    # Semantic
    # ------------------------------------------------------------------

    def _handle_semantic(self, qs):
        return self._send_error(403, "Semantic search requires a Pro or Team plan")

    # ------------------------------------------------------------------
    # Media handlers
    # ------------------------------------------------------------------

    def _handle_media_put(self, media_id):
        content_type = self.headers.get("Content-Type", "")
        raw = self._read_body()

        if "application/json" in content_type:
            data = json.loads(raw.decode("utf-8"))
            if "content_b64" in data:
                import base64
                raw = base64.b64decode(data["content_b64"])

        _media[media_id] = raw
        return self._send_json({"id": media_id, "size": len(raw), "uploaded": True})

    def _handle_media_get(self, media_id):
        if media_id not in _media:
            return self._send_error(404, "Media not found")
        return self._send_text(_media[media_id], content_type="application/octet-stream")

    def _handle_media_list(self, qs):
        ns = qs.get("ns", [None])[0]
        filter_val = qs.get("filter", [None])[0]

        items = list(_media.keys())
        if ns:
            items = [i for i in items if i.startswith(ns + "/")]
        if filter_val == "orphans":
            items = items[:1] if items else []

        result = [{"id": i, "size": len(_media[i])} for i in sorted(items)]
        return self._send_json(result)

    def _handle_media_delete(self, media_id):
        if media_id not in _media:
            return self._send_error(404, "Media not found")
        del _media[media_id]
        return self._send_json({"deleted": True, "id": media_id})

    def _handle_media_move(self, media_id):
        data = self._get_json_body()
        dst = data.get("to", "")
        if media_id not in _media:
            return self._send_error(404, "Media not found")
        _media[dst] = _media.pop(media_id)
        return self._send_json({"id": dst, "moved": True})

    def _handle_media_usage(self, media_id):
        return self._send_json([{"id": "start", "title": "Welcome"}])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Corkboard API v1 mock server")
    parser.add_argument("--port", type=int, default=18080, help="Listen port (default: 18080)")
    parser.add_argument("--host", default="127.0.0.1", help="Listen host (default: 127.0.0.1)")
    args = parser.parse_args()

    server = HTTPServer((args.host, args.port), MockHandler)
    sys.stderr.write(f"Mock server listening on http://{args.host}:{args.port}\n")
    sys.stderr.flush()
    server.serve_forever()


if __name__ == "__main__":
    main()
