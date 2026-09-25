"""Corkboard page commands — pure logic and CLI command functions.

Each command is a plain function taking (client, args). The register_pages
hook wires subparsers so the entrypoint can import without coupling to
this module's internals.
"""

import argparse
import json
import re
import sys

from cb_client import CorkboardError, build_body_payload


# ======================================================================
# Pure logic (imported by tests/test_pages_logic.py)
# ======================================================================

def apply_edits(body, edits):
    """Apply a sequence of (old, new) replacements to body.

    Each --old string must match exactly once in the (current) body.
    Edits are applied sequentially; later edits may anchor on the results
    of earlier edits.  Returns the modified body.

    Raises ValueError if any --old matches zero times or more than once.
    """
    current = body
    for old, new in edits:
        count = current.count(old)
        if count == 0:
            raise ValueError(
                f"edit string matched 0 times in the page body: {old!r}"
            )
        if count > 1:
            raise ValueError(
                f"edit string matched {count} times in the page body "
                f"(must be unique): {old!r}"
            )
        current = current.replace(old, new, 1)
    return current


def parse_edits_file(content):
    """Parse a tab-separated edits file.

    Each non-empty line is split on the first tab into (old, new).
    Empty lines and lines without a tab are skipped.

    Returns a list of (old, new) tuples.
    """
    pairs = []
    for line in content.splitlines():
        line = line.strip("\r")
        if not line:
            continue
        if "\t" not in line:
            continue
        old, new = line.split("\t", 1)
        pairs.append((old, new))
    return pairs


def _find_anchor_line(lines, anchor):
    """Find the index of the line matching *anchor* (exact match).

    Returns the 0-based index.  Raises ValueError if zero or >1 matches.
    """
    indices = [i for i, line in enumerate(lines) if line == anchor]
    if len(indices) == 0:
        raise ValueError(
            f"anchor not found in page body: {anchor!r}"
        )
    if len(indices) > 1:
        raise ValueError(
            f"anchor matched {len(indices)} lines in the page body "
            f"(must match exactly one): {anchor!r}"
        )
    return indices[0]


def _heading_level(line):
    """Return the ATX heading level (number of leading #) or None."""
    m = re.match(r"^(#{1,6})\s", line)
    if m:
        return len(m.group(1))
    return None


def resolve_anchor(body, anchor, mode, content):
    """Resolve an anchor insertion and return the modified body.

    Parameters
    ----------
    body : str
        The current page body.
    anchor : str
        The anchor line to find (must match exactly one line).
    mode : str
        One of 'under', 'after', 'before'.
    content : str
        The text to insert (should end with a newline if a separate
        paragraph is desired).

    Returns
    -------
    str
        The modified body.

    Raises ValueError if the anchor is not found, matches multiple
    lines, or the mode is unknown.
    """
    lines = body.split("\n")
    idx = _find_anchor_line(lines, anchor)

    if mode == "after":
        lines.insert(idx + 1, content.rstrip("\n"))
        return "\n".join(lines)

    if mode == "before":
        lines.insert(idx, content.rstrip("\n"))
        return "\n".join(lines)

    if mode == "under":
        level = _heading_level(lines[idx])
        if level is None:
            raise ValueError(
                f"anchor is not a Markdown ATX heading: {anchor!r}"
            )
        # Find the end of the section: the next heading of the same or
        # higher level (same or fewer #).  If none, insert at end.
        insert_at = len(lines)
        for i in range(idx + 1, len(lines)):
            hl = _heading_level(lines[i])
            if hl is not None and hl <= level:
                insert_at = i
                break
        # If inserting at EOF and the body has a trailing newline,
        # insert *before* the final empty line so we don't add a
        # spurious blank line.
        if insert_at == len(lines) and lines and lines[-1] == "":
            insert_at = len(lines) - 1
        lines.insert(insert_at, content.rstrip("\n"))
        return "\n".join(lines)

    raise ValueError(f"unknown insert mode: {mode!r}")


def is_page_not_found_error(exc):
    """Return True if the exception is a 404 page-not-found.

    Used by cmd_put to decide whether to proceed with revision=None
    (create a new page) when the page does not exist yet.
    """
    return isinstance(exc, CorkboardError) and exc.status == 404


def build_find_params(pattern, extended=False, ignore_case=False):
    """Build query params for the page find endpoint.

    Maps the CLI flags (-E / -i) to the server contract:
    ``q`` (required, string), ``regex`` (boolean), ``ignore_case`` (boolean).
    """
    params = {"q": pattern}
    if extended:
        params["regex"] = "true"
    if ignore_case:
        params["ignore_case"] = "true"
    return params


# ======================================================================
# Helpers shared by command implementations
# ======================================================================

def _read_input(args):
    """Read body text from --file, --text, or stdin (in that order)."""
    if hasattr(args, "file") and args.file:
        with open(args.file, "r") as f:
            return f.read()
    if hasattr(args, "text") and args.text is not None:
        return args.text
    # stdin
    return sys.stdin.read()


def _print_json(data):
    """Pretty-print data as JSON to stdout."""
    if isinstance(data, (dict, list)):
        print(json.dumps(data, indent=2))
    elif isinstance(data, bytes):
        print(data.decode("utf-8", errors="replace"))
    else:
        print(data)


# ======================================================================
# Command implementations
# ======================================================================

def cmd_get(client, args):
    """Get a page by id."""
    result = client.get_page(args.page)
    _print_json(result)


def cmd_put(client, args):
    """Create or replace a page.

    Fetches the current revision first (for CAS), then PUTs the new body.
    If the page does not exist (404), proceeds with revision=None to create.
    """
    body = _read_input(args)
    try:
        page = client.get_page(args.page)
        revision = page.get("revision") or page.get("body_revision")
    except CorkboardError as e:
        if is_page_not_found_error(e):
            revision = None
        else:
            raise
    result = client.put_cas(
        args.page, body, revision=revision, summary=getattr(args, "sum", None)
    )
    _print_json(result)


def cmd_append(client, args):
    """Append text to a page."""
    body = _read_input(args)
    payload = build_body_payload(body, getattr(args, "sum", None))
    result = client.post(f"pages/{args.page}/append", data=payload)
    _print_json(result)


def cmd_delete(client, args):
    """Delete a page."""
    result = client.delete(f"pages/{args.page}")
    _print_json(result)


def cmd_edit(client, args):
    """Edit a page with one or more --old/--new replacements.

    Passes a mutation callback to put_cas so that, on a CAS conflict, the
    edits are re-applied against the freshly re-fetched body (never against
    the pre-conflict snapshot).
    """
    # Collect edits from --old/--new pairs and optionally --edits file
    edits = []
    if hasattr(args, "old") and args.old:
        if not args.new or len(args.old) != len(args.new):
            raise CorkboardError("--old and --new must be paired 1:1")
        edits = list(zip(args.old, args.new))
    if hasattr(args, "edits") and args.edits:
        with open(args.edits, "r") as f:
            file_edits = parse_edits_file(f.read())
        edits.extend(file_edits)

    if not edits:
        raise CorkboardError("no edits provided (use --old/--new or --edits)")

    def mutate(fresh_body):
        return apply_edits(fresh_body, edits)

    result = client.put_cas(args.page, mutate, summary=getattr(args, "sum", None))
    _print_json(result)


def cmd_insert(client, args):
    """Insert content at an anchor position.

    Passes a mutation callback to put_cas so that, on a CAS conflict, the
    insert is re-resolved against the freshly re-fetched body.
    """
    content = _read_input(args)

    # Determine mode
    mode = None
    anchor = None
    if hasattr(args, "under") and args.under:
        mode = "under"
        anchor = args.under
    elif hasattr(args, "after") and args.after:
        mode = "after"
        anchor = args.after
    elif hasattr(args, "before") and args.before:
        mode = "before"
        anchor = args.before
    else:
        raise CorkboardError("one of --under, --after, or --before is required")

    def mutate(fresh_body):
        return resolve_anchor(fresh_body, anchor, mode, content)

    result = client.put_cas(args.page, mutate, summary=getattr(args, "sum", None))
    _print_json(result)


def cmd_find(client, args):
    """Search for a pattern in a page's body."""
    extended = bool(getattr(args, "extended", False))
    ignore_case = bool(getattr(args, "i", False))
    params = build_find_params(args.pattern, extended=extended, ignore_case=ignore_case)
    result = client.get(f"pages/{args.page}/find", params=params)
    _print_json(result)


def cmd_move(client, args):
    """Move/rename a page."""
    result = client.post(
        f"pages/{args.src}/move",
        data={"to": args.dst, "rewrite": True},
    )
    _print_json(result)


def cmd_links(client, args):
    """List outgoing links from a page."""
    result = client.get(f"pages/{args.page}/links")
    _print_json(result)


def cmd_backlinks(client, args):
    """List backlinks to a page."""
    result = client.get(f"pages/{args.page}/backlinks")
    _print_json(result)


def cmd_revisions(client, args):
    """List revisions of a page."""
    result = client.get(f"pages/{args.page}/revisions")
    _print_json(result)


def cmd_revision_show(client, args):
    """Show a specific revision of a page."""
    result = client.get(f"pages/{args.page}/revisions/{args.rev}")
    _print_json(result)


# ======================================================================
# Subparser registration (seam for entrypoint)
# ======================================================================

def _add_output_args(parser):
    """Add --sum (edit summary) argument to a parser."""
    parser.add_argument("--sum", default=None, help="Edit summary")


def _add_input_args(parser):
    """Add --file and --text input arguments to a parser."""
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--file", "-F", default=None, help="Read body from file")
    group.add_argument("--text", "-T", default=None, help="Body text inline")


def register_pages(subparsers, client_factory):
    """Register all page subcommands on the given argparse subparsers.

    Parameters
    ----------
    subparsers : argparse._SubParsersAction
        The subparsers object from the top-level parser.
    client_factory : callable
        A zero-argument callable that returns a CorkboardClient.
    """
    # --- get ---
    p_get = subparsers.add_parser("get", help="Get a page by id")
    p_get.add_argument("page", help="Page id (e.g. playground/smoke)")
    p_get.set_defaults(func=lambda args: cmd_get(client_factory(), args))

    # --- put ---
    p_put = subparsers.add_parser("put", help="Create or replace a page")
    p_put.add_argument("page", help="Page id")
    _add_input_args(p_put)
    _add_output_args(p_put)
    p_put.set_defaults(func=lambda args: cmd_put(client_factory(), args))

    # --- append ---
    p_append = subparsers.add_parser("append", help="Append text to a page")
    p_append.add_argument("page", help="Page id")
    _add_input_args(p_append)
    _add_output_args(p_append)
    p_append.set_defaults(func=lambda args: cmd_append(client_factory(), args))

    # --- delete ---
    p_delete = subparsers.add_parser("delete", help="Delete a page")
    p_delete.add_argument("page", help="Page id")
    p_delete.set_defaults(func=lambda args: cmd_delete(client_factory(), args))

    # --- edit ---
    p_edit = subparsers.add_parser("edit", help="Edit a page (fetch-mutate-put)")
    p_edit.add_argument("page", help="Page id")
    p_edit.add_argument("--old", "-O", action="append", default=None,
                        help="Old string (can be repeated)")
    p_edit.add_argument("--new", "-N", action="append", default=None,
                        help="New string (can be repeated)")
    p_edit.add_argument("--edits", default=None,
                        help="File with tab-separated old/new pairs")
    _add_output_args(p_edit)
    p_edit.set_defaults(func=lambda args: cmd_edit(client_factory(), args))

    # --- insert ---
    p_insert = subparsers.add_parser("insert", help="Insert content at an anchor")
    p_insert.add_argument("page", help="Page id")
    anchor_group = p_insert.add_mutually_exclusive_group(required=True)
    anchor_group.add_argument("--under", default=None,
                              help="Insert under a heading")
    anchor_group.add_argument("--after", default=None,
                              help="Insert after a line")
    anchor_group.add_argument("--before", default=None,
                              help="Insert before a line")
    _add_input_args(p_insert)
    _add_output_args(p_insert)
    p_insert.set_defaults(func=lambda args: cmd_insert(client_factory(), args))

    # --- find ---
    p_find = subparsers.add_parser("find", help="Search a page body")
    p_find.add_argument("page", help="Page id")
    p_find.add_argument("pattern", help="Pattern to search for")
    p_find.add_argument("-E", "--extended", action="store_true",
                        help="Extended regex")
    p_find.add_argument("-i", action="store_true",
                        help="Case-insensitive search")
    p_find.set_defaults(func=lambda args: cmd_find(client_factory(), args))

    # --- move ---
    p_move = subparsers.add_parser("move", help="Move/rename a page")
    p_move.add_argument("src", help="Source page id")
    p_move.add_argument("dst", help="Destination page id")
    p_move.set_defaults(func=lambda args: cmd_move(client_factory(), args))

    # --- links ---
    p_links = subparsers.add_parser("links", help="List outgoing links")
    p_links.add_argument("page", help="Page id")
    p_links.set_defaults(func=lambda args: cmd_links(client_factory(), args))

    # --- backlinks ---
    p_backlinks = subparsers.add_parser("backlinks", help="List backlinks")
    p_backlinks.add_argument("page", help="Page id")
    p_backlinks.set_defaults(func=lambda args: cmd_backlinks(client_factory(), args))

    # --- revisions ---
    p_revisions = subparsers.add_parser("revisions", help="List page revisions")
    p_revisions.add_argument("page", help="Page id")
    p_revisions.set_defaults(func=lambda args: cmd_revisions(client_factory(), args))

    # --- revision-show ---
    p_rshow = subparsers.add_parser("revision-show", help="Show a revision")
    p_rshow.add_argument("page", help="Page id")
    p_rshow.add_argument("rev", help="Revision identifier")
    p_rshow.set_defaults(func=lambda args: cmd_revision_show(client_factory(), args))