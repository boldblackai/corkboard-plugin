"""Corkboard HTTP API v1 client — stdlib-only.

Configuration:

* ``CORKBOARD_TOKEN`` — required; a user-scoped personal access token
  (``cb_…``).  One token reaches every workspace the user can access.
* ``CORKBOARD_URL`` — optional; base URL, defaults to https://corkboard.wiki
* ``CORKBOARD_WORKSPACE`` — optional; default workspace as ``org_slug/ws_slug``

Content requests are workspace-scoped:

    {base}/api/v1/o/{org_slug}/{ws_slug}/{path}

The ``me`` endpoint stays unscoped (``{base}/api/v1/me``): it is the
discovery call that lists the token's accessible ``org/ws`` pairs.
"""

import json
import os
import urllib.error
import urllib.parse
import urllib.request


class CorkboardError(Exception):
    """Typed error carrying HTTP status code and response body."""

    def __init__(self, message, status=None, body=None):
        super().__init__(message)
        self.status = status
        self.body = body

    def __str__(self):
        base = super().__str__()
        if self.status is not None:
            base = f"HTTP {self.status}: {base}"
        return base


def parse_workspace_spec(spec):
    """Parse an ``org_slug/ws_slug`` workspace spec into ``(org, ws)``.

    Leading/trailing slashes are tolerated.  Returns ``None`` for an empty
    or missing spec.  Raises ``CorkboardError`` for anything that is not
    exactly two non-empty segments.
    """
    if spec is None:
        return None
    spec = spec.strip()
    if not spec:
        return None
    parts = [part for part in spec.strip("/").split("/")]
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise CorkboardError(
            f"invalid workspace {spec!r}: expected 'org_slug/ws_slug'"
        )
    return (parts[0], parts[1])


def workspace_pairs_from_me(payload):
    """Extract the accessible ``(org_slug, ws_slug)`` pairs from a /me payload.

    The server shape (corkboard-app 21b7552) is::

        {"workspaces": [{"org": {"slug": …}, "ws": {"slug": …}}, …]}

    Malformed entries are skipped rather than raising, so a partial payload
    degrades to "no accessible workspace" instead of a crash.
    """
    pairs = []
    if not isinstance(payload, dict):
        return pairs
    entries = payload.get("workspaces")
    if not isinstance(entries, list):
        return pairs
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        org = entry.get("org")
        ws = entry.get("ws")
        org_slug = org.get("slug") if isinstance(org, dict) else None
        ws_slug = ws.get("slug") if isinstance(ws, dict) else None
        if org_slug and ws_slug:
            pairs.append((org_slug, ws_slug))
    return pairs


def build_body_payload(body, summary=None):
    """Build a page body write payload, including ``summary`` when provided.

    Shared by the CAS PUT path (put_cas) and the append path so the ``--sum``
    edit summary reaches the server's ``summary`` field (verified in
    PageController.validatePage + append, which accept an optional summary).
    """
    payload = {"body": body}
    if summary is not None:
        payload["summary"] = summary
    return payload


class CorkboardClient:
    """HTTP client for Corkboard API v1.

    Reads CORKBOARD_TOKEN (required) and CORKBOARD_URL (optional, default
    https://corkboard.wiki) from environment.

    The workspace is resolved once, in this order:

    1. the ``workspace`` constructor argument (``"org_slug/ws_slug"``)
    2. the ``CORKBOARD_WORKSPACE`` environment variable (same form)
    3. the first accessible pair from ``GET /api/v1/me``

    An empty (3) result raises ``CorkboardError``.  The resolved pair is
    cached on the instance, so ``me`` is called at most once per client.
    """

    def __init__(self, base_url=None, token=None, workspace=None):
        self._base_url = (
            base_url or os.environ.get("CORKBOARD_URL", "https://corkboard.wiki")
        ).rstrip("/")
        self._token = token or os.environ.get("CORKBOARD_TOKEN", "")
        if not self._token:
            raise CorkboardError(
                "CORKBOARD_TOKEN environment variable is not set"
            )
        # Resolution order step 1 (explicit arg) and step 2 (env).
        if workspace is None:
            workspace = os.environ.get("CORKBOARD_WORKSPACE")
        self._workspace = parse_workspace_spec(workspace)
        self._me_payload = None

    @property
    def base_url(self):
        return self._base_url

    @property
    def workspace(self):
        """The resolved ``(org_slug, ws_slug)`` pair (me-derived and cached)."""
        if self._workspace is None:
            self._workspace = self._resolve_default_workspace()
        return self._workspace

    def _resolve_default_workspace(self):
        """Resolution order step 3: first accessible pair from /api/v1/me."""
        pairs = workspace_pairs_from_me(self.me())
        if not pairs:
            raise CorkboardError(
                "no accessible workspace for this token: "
                "check the token's scopes or set CORKBOARD_WORKSPACE=org_slug/ws_slug"
            )
        return pairs[0]

    def me(self, refresh=False):
        """GET /api/v1/me — unscoped identity + accessible workspaces.

        Cached per instance; pass ``refresh=True`` to re-fetch.
        """
        if self._me_payload is None or refresh:
            self._me_payload = self.request("GET", "me", unscoped=True)
        return self._me_payload

    def _build_url(self, path, params=None, unscoped=False):
        """Build a full API URL from a path and optional query params.

        Content paths are workspace-scoped (``o/{org}/{ws}/…``); ``unscoped``
        is reserved for service-level endpoints such as ``me`` and the root.
        """
        path = path.lstrip("/")
        if unscoped:
            url = f"{self._base_url}/api/v1/{path}"
        else:
            org, ws = self.workspace
            org = urllib.parse.quote(org, safe="")
            ws = urllib.parse.quote(ws, safe="")
            url = f"{self._base_url}/api/v1/o/{org}/{ws}/{path}"
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        return url

    def _make_request(self, method, url, data=None, headers=None):
        """Low-level HTTP request. Returns (status, body_bytes)."""
        req_headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        if headers:
            req_headers.update(headers)

        body_bytes = None
        if data is not None:
            if isinstance(data, str):
                body_bytes = data.encode("utf-8")
            elif isinstance(data, bytes):
                body_bytes = data
            else:
                body_bytes = json.dumps(data).encode("utf-8")
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"

        req = urllib.request.Request(
            url, data=body_bytes, headers=req_headers, method=method
        )

        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as e:
            body = e.read()
            raise CorkboardError(
                f"{e.reason}",
                status=e.code,
                body=body,
            )

    def request(self, method, path, data=None, headers=None, params=None,
                raw=False, unscoped=False):
        """Make an HTTP request to the API.

        Returns parsed JSON by default. When ``raw=True``, returns the raw
        response body bytes (used for binary downloads such as media-get).
        ``unscoped=True`` bypasses the workspace base-path (only ``me`` and
        the service root are unscoped).
        """
        url = self._build_url(path, params, unscoped=unscoped)
        status, body = self._make_request(method, url, data=data, headers=headers)
        if raw:
            return body
        try:
            return json.loads(body)
        except (json.JSONDecodeError, ValueError):
            return body

    def get(self, path, params=None, unscoped=False):
        """GET request to the API. Returns parsed JSON."""
        return self.request("GET", path, params=params, unscoped=unscoped)

    def post(self, path, data=None, params=None):
        """POST request to the API. Returns parsed JSON."""
        return self.request("POST", path, data=data, params=params)

    def put(self, path, data=None, params=None):
        """PUT request to the API. Returns parsed JSON."""
        return self.request("PUT", path, data=data, params=params)

    def delete(self, path, params=None):
        """DELETE request to the API. Returns parsed JSON."""
        return self.request("DELETE", path, params=params)

    # ------------------------------------------------------------------
    # Optimistic concurrency (CAS) helpers
    # ------------------------------------------------------------------

    def get_page(self, page_id):
        """Fetch a page by id. Returns the full page JSON (incl. body + revision)."""
        return self.get(f"pages/{page_id}")

    def put_cas(self, page_id, body_or_mutate, revision=None, summary=None):
        """PUT a page with optimistic concurrency (CAS).

        ``body_or_mutate`` is either:

          - a ``str``: the full replacement body (replacement semantics — a
            concurrent change to the page does not alter the intent to replace
            the whole body), or
          - a callable ``f(fresh_body) -> str``: a mutation to (re-)apply to
            the current page body (used by edit/insert).

        On HTTP 412 the page is re-fetched ONCE and the write is re-applied
        against the fresh body:

          - callable: ``new_body = body_or_mutate(fresh_body)``
          - string:   the same body is re-sent (full replacement)

        A second 412 raises ``CorkboardError`` (status 412) with a CONFLICT
        message. ``summary``, when set, is sent as the page's edit summary.
        """
        return self._put_cas_internal(page_id, body_or_mutate, revision, summary, attempt=1)

    def _put_cas_internal(self, page_id, body_or_mutate, revision, summary, attempt):
        headers = {}
        body = body_or_mutate

        # A mutation callback needs the current body to derive the write;
        # fetch it now (and its revision, when the caller did not pin one).
        if callable(body_or_mutate):
            page = self.get_page(page_id)
            fresh_body = page.get("body", "")
            if revision is None:
                revision = page.get("revision") or page.get("body_revision")
            body = body_or_mutate(fresh_body)

        if revision is not None:
            headers["If-Match"] = f'"{revision}"'

        try:
            return self._put_page(page_id, body, summary, headers)
        except CorkboardError as e:
            if e.status != 412:
                raise
            if attempt >= 2:
                # Second 412 — a genuine conflict we will not resolve.
                raise CorkboardError(
                    "CONFLICT: page was modified by another writer",
                    status=412,
                    body=e.body,
                )
            # Re-fetch the page ONCE and re-apply against the fresh body.
            page = self.get_page(page_id)
            new_revision = page.get("revision") or page.get("body_revision")
            fresh_body = page.get("body", "")
            new_body = (
                body_or_mutate(fresh_body) if callable(body_or_mutate) else body
            )
            return self._put_cas_internal(
                page_id, new_body, new_revision, summary, attempt=attempt + 1
            )

    def _put_page(self, page_id, body, summary, headers):
        """PUT a page body with the given CAS headers and summary."""
        payload = build_body_payload(body, summary)
        return self.request("PUT", f"pages/{page_id}", data=payload, headers=headers)
