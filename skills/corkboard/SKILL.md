---
name: corkboard
description: Use when the user wants to read/write Corkboard wikis via the HTTP API.
version: "2.0.0"
license: MIT
---

# Corkboard Skill

A stdlib-only Python CLI that reads and writes Corkboard wikis over the
**HTTP API v1** with an API token.  Designed for CLI-driven agents
(Hermes, Claude Code, Codex, …) — an alternative to MCP for
script-and-shell workflows.

## Breaking change (v2)

Since corkboard-app #214 the API is **workspace-scoped**:

```
content:  {CORKBOARD_URL}/api/v1/o/{org_slug}/{ws_slug}/{path}
me:       {CORKBOARD_URL}/api/v1/me          (unscoped)
```

The old unscoped content grammar (`/api/v1/{path}`) returns **404**.
This release re-points every content request to the new base path.
If your commands began failing with 404 after the server rollout,
upgrade the skill.  PATs are **user-scoped** now: one token reaches
every workspace the user can access (power = token scopes ∩ the live
effective role in that workspace).

## Setup

1. **Get an API token.**  Open Corkboard, go to **Settings → API
   tokens**, and create a personal access token (`cb_…`).  The token is
   **user-scoped** — it is not bound to a single workspace — and is used
   as a Bearer token.

2. **Set the environment variables** in your agent's shell profile:

   ```bash
   export CORKBOARD_TOKEN="cb_your_token_here"
   ```

   `CORKBOARD_TOKEN` is required — the CLI will exit with a clear error
   if it is missing.

   Content endpoints need an `org_slug/ws_slug` pair.  The CLI resolves
   one in this order:

   1. the `--workspace ORG/WS` flag,
   2. the optional `CORKBOARD_WORKSPACE` environment variable:

      ```bash
      export CORKBOARD_WORKSPACE="acme/main"
      ```

   3. the first accessible workspace from `me` (below), resolved once
      and cached per client.

   Run `me` to discover your slugs.  When none of the three yields a
   workspace, the CLI exits with
   `no accessible workspace for this token`.

   For self-hosted or development instances, you may optionally set
   `CORKBOARD_URL` to override the default (`https://corkboard.wiki`):

   ```bash
   export CORKBOARD_URL="https://your-instance.example.com"
   ```

3. **Install the skill.**  Clone or curl the skill into your agent's
   skills directory (see [README.md](README.md) for one-liners).

4. **Verify.**  `python3 script/corkboard.py list` should print your
   workspace's top-level pages.

## Command Reference

Invoke every command through the entrypoint:

```
python3 script/corkboard.py <command> [options]
```

### Discovery

`me` is the only command that is **not** workspace-scoped.  It is the
discovery tool: it lists the token's identity and every accessible
`org/ws` pair, and it backs the default workspace resolution.

| Command | Arguments | Description |
|---------|-----------|-------------|
| `me` | [`--json`] | Show the token's user, plan, and accessible `org/ws` workspaces. Use it to find the slugs for `--workspace` / `CORKBOARD_WORKSPACE`. `--json` prints the raw `/me` payload. |

Every other command targets the workspace base path
`{CORKBOARD_URL}/api/v1/o/{org_slug}/{ws_slug}/…`.

### Pages

| Command | Arguments | Description |
|---------|-----------|-------------|
| `get` | `<page>` | Fetch a page by id (JSON to stdout). |
| `put` | `<page>` [`--file PATH` \| `--text TEXT`] [`--sum MSG`] | Create or replace a page. Reads body from `--file`, `--text`, or stdin. Uses CAS (If-Match) to avoid overwriting concurrent edits. |
| `append` | `<page>` [`--file PATH` \| `--text TEXT`] [`--sum MSG`] | Append text to the end of a page. |
| `delete` | `<page>` | Delete a page. |
| `edit` | `<page>` (`--old OLD --new NEW` …) [`--edits FILE`] [`--sum MSG`] | Surgical edit. Fetch-mutate-put_cas flow: each `--old` must match exactly once in the current body; pairs are applied sequentially. Use `--edits` for a tab-separated file (`old\tnew` per line). |
| `insert` | `<page>` (`--under H` \| `--after L` \| `--before L`) [`--file PATH` \| `--text TEXT`] [`--sum MSG`] | Insert content at an anchor position. `--under` places content under a Markdown heading (ends at next same-or-higher-level heading). `--after` and `--before` target an exact line. The anchor must match exactly one line. |
| `find` | `<page>` `<pattern>` [`-E`] [`-i`] | Search a page body. `-E` enables extended regex; `-i` makes the search case-insensitive. |
| `move` | `<src>` `<dst>` | Move/rename a page. Server-side: backlinks are rewritten and history is preserved. |
| `links` | `<page>` | List outgoing wiki-links from a page. |
| `backlinks` | `<page>` | List pages that link to this page. |
| `revisions` | `<page>` | List revision history for a page. |
| `revision-show` | `<page>` `<rev>` | Show a specific revision's content. |

### Collections & Search

| Command | Arguments | Description |
|---------|-----------|-------------|
| `list` | [`--ns NS`] [`--depth N`] | List pages in a namespace. `--depth 0` = full recursion. |
| `search` | `<query>` [`--ns NS`] | Full-text search across pages. |
| `sitemap` | [`--ns NS`] [`--depth N`] | Render an ASCII tree of the page hierarchy. |
| `wanted` | *(none)* | List pages referenced by internal links but not yet created. |
| `orphans` | *(none)* | List pages with no inbound links. |
| `semantic` | `<query>` | Semantic (vector) search. On Free plans this may be unavailable (exits gracefully with a message). |

### Media

| Command | Arguments | Description |
|---------|-----------|-------------|
| `media-upload` | `<file>` `<ns>` `<name>` | Upload a file. Tries raw binary PUT first; falls back to base64 JSON on content-type rejection. |
| `media-get` | `<id>` [`-o PATH`] | Download a media file. Writes to `-o` path or stdout. |
| `media-list` | [`--ns NS`] | List media files, optionally filtered by namespace. |
| `media-delete` | `<id>` | Delete a media file. |
| `media-move` | `<src>` `<dst>` | Move/rename a media file. |
| `media-orphans` | *(none)* | List media files not referenced by any page. |
| `media-usage` | `<id>` | Show which pages reference a media file. |

### Common Flags

| Flag | Applies to | Meaning |
|------|-----------|---------|
| `--workspace ORG/WS` | every command (before the command name) | Workspace to target. Wins over `CORKBOARD_WORKSPACE` and the `me`-derived default. |
| `--sum MSG` | `put`, `append`, `edit`, `insert` | Edit summary recorded in the page revision. |
| `--file PATH` / `-F` | `put`, `append`, `insert` | Read body from a file (mutually exclusive with `--text`). |
| `--text TEXT` / `-T` | `put`, `append`, `insert` | Inline body text (mutually exclusive with `--file`). |

When neither `--file` nor `--text` is given, the body is read from **stdin**.

## Conventions

### Page IDs and Linking

Corkboard page IDs use `/`-separated paths (e.g. `projects/alpha`).
Wiki-links use GFM syntax: `[link text](page_id)`.  By convention:

- **Absolute-link default.**  `[Projects](projects/start)` links to the
  root-level `projects/start`, regardless of the current page's
  namespace.  This keeps the link graph simple and predictable.

- **Dense link graph.**  Every page should link to at least one other
  page and be linked from another.  Use the `wanted` and `orphans`
  commands regularly to find gaps.

- **Self-describing pages.**  Every page should open with a level-1
  heading (`# Title`) and a one-paragraph summary.  This makes search
  results and link previews useful.

- **Shallow namespaces.**  Prefer one or two levels of nesting
  (`projects/`, `projects/reports`) over deep hierarchies.

### Edit Safety

All mutating page commands use **optimistic concurrency (CAS):**

1. Fetch the current revision of the page.
2. Apply the mutation locally.
3. PUT with `If-Match: "<revision>"`.
4. On HTTP 412 (conflict), re-fetch once and retry.
5. On a second 412, fail with `CONFLICT`.
The `edit` command additionally enforces **unique-match assertions:**
every `--old` string must appear exactly once in the page body.  This
prevents accidental or ambiguous replacements.  The `insert` command
requires the anchor line to match exactly one line.

### Gardening

Keep the workspace healthy with periodic sweeps:

```
python3 script/corkboard.py wanted         # broken internal links
python3 script/corkboard.py orphans        # pages with no inbound links
python3 script/corkboard.py media-orphans  # unreferenced media files
```

**Link-health check.**  After editing a page, verify its links:

```
python3 script/corkboard.py links <page>   # get the server-side link list
```

Diff the output against the wiki-links in the page body to catch typos
and dangling references.  There is no dedicated link-health endpoint;
the CLI-side equivalent is `links` + manual diff.

### Page-Naming Hygiene

- Use lowercase, hyphen-separated words (`getting-started`, not
  `GettingStarted` or `getting_started`).
- Start pages are named `start` within their namespace.
- Avoid special characters except `/` (namespace separator) and `-`
  (word separator).

## Zero Dependencies

This skill uses only the Python 3 standard library (`urllib`, `json`,
`argparse`, `http.server` for testing).  No pip packages, no SDKs, no
MCP client libraries.  Python 3.11+.

## Product Docs

- [Corkboard Skill documentation](https://corkboard.wiki/docs/skill) —
  install, configuration, and usage.
- [Corkboard workspace](https://corkboard.wiki) — create an account and
  workspace.