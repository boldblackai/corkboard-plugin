# Corkboard Plugin

[Corkboard](https://corkboard.wiki) is a fast, Markdown-native wiki with
full-text and semantic search. This plugin connects your agent to your
Corkboard workspace two ways:

1. **MCP server** (zero-config) — remote tools at `https://corkboard.wiki/mcp`
   for reading, writing, searching, and moving pages. Authentication is OAuth
   2.1: on first use, the client discovers Corkboard's OAuth endpoints
   automatically (`/.well-known/oauth-protected-resource/mcp`) and walks the
   authorization-code + PKCE flow. No tokens are stored in this repository.
2. **CLI skill** (`skills/corkboard/`) — a zero-dependency, stdlib-only Python
   CLI over the Corkboard HTTP API v1. It covers the full surface including
   media upload/download, wiki gardening reports (orphans, wanted pages), and
   surgical anchored edits. Requires an API token
   (**Settings → API tokens** in Corkboard) in `CORKBOARD_TOKEN`.

Both paths talk to the same workspace-scoped backend. If the MCP server is
connected, use it for page reads/writes and search; use the CLI when a task
needs the fuller command surface (media management, gardening reports,
revision inspection) or when you prefer plain shell commands.

## Install (VS Code / Cursor via Open VSX)

This repository ships a VS Code / Cursor extension, listed on
[Open VSX](https://open-vsx.org/) as `boldblack.corkboard`:

1. In VS Code or Cursor, open the Extensions view and search for **Corkboard**.
2. Install **Corkboard** (`boldblack.corkboard`) and reload the window.
3. Ask your agent for a Corkboard action (for example *list pages*), then
   approve the MCP OAuth prompt on first use.

The extension contributes the MCP server definition provider
`corkboardMcpProvider` (label `Corkboard`), which points at the remote server
`https://corkboard.wiki/mcp`. OAuth 2.1 discovery and consent are handled by
the client, so no credentials are stored in the extension. The bundled
`skills/corkboard` CLI skill is included in the VSIX.

## Install (Cursor)

Install from the Cursor Marketplace (search "Corkboard"), or add this
repository as a plugin source manually.

After install, trigger any Corkboard tool (for example *list pages*) and
complete the OAuth consent screen. The requested scope is `mcp:read` for
read-only use; agents that write pages need `mcp:write` as well.

## Install (Claude Code)

This repository doubles as a Claude Code plugin marketplace:

```bash
claude plugin marketplace add boldblackai/corkboard-plugin
claude plugin install corkboard@corkboard-plugin
```

The MCP server connects as a remote connector (`/mcp`) — approve the OAuth
prompt on first use. The skill runs as `/corkboard:corkboard`; the CLI half
reads `CORKBOARD_TOKEN` from the environment.

## Install (other agents)

The skill half is portable to any agent that supports the Agent Skills format
or can run shell commands:

```bash
git clone https://github.com/boldblackai/corkboard-plugin.git
# then point your agent at ./skills/corkboard/SKILL.md
```

The MCP half works with any MCP client that speaks Streamable HTTP and OAuth
2.1 discovery: `https://corkboard.wiki/mcp`.

## Plugin layout

```
corkboard-plugin/
├── package.json              # VS Code / Cursor (Open VSX) extension manifest
├── plugin.json               # Agent Plugins (open standard) manifest
├── mcp.json                  # Agent Plugins MCP config (streamable-http)
├── .cursor-plugin/
│   └── plugin.json           # Cursor plugin manifest (marketplace metadata)
├── mcp.cursor.json           # Cursor MCP config (referenced by manifest)
├── .claude-plugin/
│   ├── plugin.json           # Claude Code plugin manifest
│   └── marketplace.json      # makes this repo a Claude Code marketplace
├── .mcp.json                 # Claude Code MCP config (remote connector)
├── src/
│   └── extension.ts          # extension entry: registers corkboardMcpProvider
├── out/
│   └── extension.js          # committed build of src/extension.ts (shipped)
├── skills/
│   └── corkboard/
│       ├── SKILL.md          # vendored from boldblackai/corkboard-skill
│       └── script/           # stdlib-only Python CLI
├── assets/
│   ├── logo.svg              # Corkboard orbit mark (BBS-001)
│   └── icon.png              # 128x128 marketplace icon (from logo.svg)
└── scripts/
    └── validate.py           # manifest + structure validator (CI gate)
```

## Skill configuration

```bash
export CORKBOARD_TOKEN="cb_your_api_token"   # Settings → API tokens
export CORKBOARD_URL="https://corkboard.wiki"  # optional; default
export CORKBOARD_WORKSPACE="org/ws"          # optional; see `me`
```

Full CLI reference: [`skills/corkboard/SKILL.md`](skills/corkboard/SKILL.md).

## Development

```bash
python3 scripts/validate.py    # validate manifests + skill frontmatter
python3 tests/test_validate.py # validator unit tests (extension manifest checks)
node tests/extension_runtime.test.js  # compiled entry point, stubbed host API

# vendored-skill test suite (from boldblackai/corkboard-skill)
cd skills/corkboard
python3 tests/test_pages_logic.py            # + 3 more test_*.py
python3 tests/mock_server.py --port 8765 &   # stdlib mock of API v1
python3 tests/smoke_matrix.py --port 8765    # 43-command CLI matrix

# VSIX build (Open VSX / VS Code). The committed out/extension.js is the
# compiled src/extension.ts; regenerate it after editing the source:
cd "$(git rev-parse --show-toplevel)"
npm install            # devDependencies only; no runtime dependencies
npm run compile        # tsc -p tsconfig.json -> out/extension.js
npx @vscode/vsce package --no-dependencies   # -> corkboard-<version>.vsix
```

CI runs the validator, the validator unit tests, a stdlib-only import audit
over script + tests, the four unit suites, the mock-server command matrix, the
extension entry point test, and `vsce package --no-dependencies` on every push.

The `skills/corkboard/` subtree is vendored from
[boldblackai/corkboard-skill](https://github.com/boldblackai/corkboard-skill).
Do not edit it here — change it upstream and re-vendor:

```bash
git subtree pull --prefix skills/corkboard \
  https://github.com/boldblackai/corkboard-skill.git main --squash
```

## Security

- No credentials, tokens, or secrets live in this repository.
- The MCP server uses OAuth 2.1 with PKCE; tokens never transit the plugin.
- The CLI skill reads `CORKBOARD_TOKEN` from the environment only.
- The MCP scope model is least-privilege: `mcp:read` (read-only delegation),
  `mcp:write` (mutations), `mcp:admin` (reserved).

## License

MIT — see [LICENSE](LICENSE).
