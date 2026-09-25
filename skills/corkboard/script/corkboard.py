#!/usr/bin/env python3
"""Corkboard CLI — unified entrypoint for all skill commands."""

import os
import sys

# Bootstrap: add the script/ directory to the path so we can import
# the sibling modules regardless of how the CLI is invoked.
_script_dir = os.path.dirname(os.path.abspath(__file__))
if _script_dir not in sys.path:
    sys.path.insert(0, _script_dir)

import argparse

from cb_client import CorkboardClient, CorkboardError
from cb_pages import register_pages
from cb_collections import register_collections
from cb_media import register_media
from cb_me import register_me


# Set from --workspace in main() before any command runs.  Keeps the
# client_factory contract (zero-argument callable) unchanged for the
# command modules.
_WORKSPACE_OVERRIDE = None


def _mk_client():
    """Factory: build a CorkboardClient from environment (+ --workspace)."""
    return CorkboardClient(workspace=_WORKSPACE_OVERRIDE)


def main(argv=None):
    global _WORKSPACE_OVERRIDE

    if argv is None:
        argv = sys.argv[1:]

    parser = argparse.ArgumentParser(
        prog="corkboard",
        description="Corkboard CLI — interact with the Corkboard API v1.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Show the service root response and exit",
    )
    parser.add_argument(
        "--workspace",
        default=None,
        metavar="ORG/WS",
        help=("Workspace to target as 'org_slug/ws_slug' "
              "(default: $CORKBOARD_WORKSPACE, else the first accessible "
              "workspace from 'me')"),
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="<command>",
    )

    register_pages(subparsers, _mk_client)
    register_collections(subparsers, _mk_client)
    register_media(subparsers, _mk_client)
    register_me(subparsers, _mk_client)

    args = parser.parse_args(argv)

    # Explicit CLI workspace wins over CORKBOARD_WORKSPACE (see _mk_client).
    _WORKSPACE_OVERRIDE = args.workspace

    # --version flag
    if args.version:
        try:
            client = _mk_client()
            root = client.get("", unscoped=True)
            import json
            print(json.dumps(root, indent=2))
        except CorkboardError as e:
            print(f"corkboard: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # No command given
    if not hasattr(args, "func") or args.func is None:
        parser.print_help()
        sys.exit(2)

    try:
        args.func(args)
    except CorkboardError as e:
        print(f"corkboard: {e}", file=sys.stderr)
        sys.exit(1)
    except ValueError as e:
        # argparse or user-input level errors
        print(f"corkboard: {e}", file=sys.stderr)
        sys.exit(2)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
