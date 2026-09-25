#!/usr/bin/env python3
"""``me`` command — token identity + accessible workspaces.

``GET /api/v1/me`` is the discovery endpoint and is NOT workspace-scoped:
it returns the token's user, its default organization/workspace, the plan,
and every accessible ``org/ws`` pair.  The first pair is the client's
me-derived default workspace.
"""

from __future__ import annotations

import json

from cb_client import workspace_pairs_from_me


def format_me(payload):
    """Render a /me payload as human-readable text (identity + pairs)."""
    if not isinstance(payload, dict):
        payload = {}
    user = payload.get("user") or {}
    org = payload.get("organization") or {}
    ws = payload.get("workspace") or {}

    lines = [f"user: {user.get('name') or user.get('email') or 'unknown'}"]
    if user.get("email"):
        lines.append(f"email: {user['email']}")
    if payload.get("plan"):
        lines.append(f"plan: {payload['plan']}")
    if org.get("slug") and ws.get("slug"):
        lines.append(f"current workspace: {org['slug']}/{ws['slug']}")

    pairs = workspace_pairs_from_me(payload)
    lines.append(f"accessible workspaces ({len(pairs)}):")
    if pairs:
        lines.extend(f"  {org_slug}/{ws_slug}" for org_slug, ws_slug in pairs)
    else:
        lines.append("  (none)")

    return "\n".join(lines)


def cmd_me(client, args):
    """Print this token's identity and accessible workspaces."""
    payload = client.me(refresh=True)
    if getattr(args, "json", False):
        print(json.dumps(payload, indent=2))
        return
    print(format_me(payload))


def register_me(subparsers, client_factory):
    """Register the unscoped ``me`` discovery command."""
    p_me = subparsers.add_parser(
        "me",
        help="Show token identity and accessible workspaces (org/ws pairs)",
    )
    p_me.add_argument("--json", action="store_true",
                      help="Print the raw /me payload as JSON")
    p_me.set_defaults(func=lambda args: cmd_me(client_factory(), args))
