# Changelog

All notable changes to the Corkboard extension are noted in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [0.1.0]

### Added

- Initial release of the Corkboard extension (`boldblackai.corkboard`) on Open VSX.
- Contributes the MCP server definition provider `corkboardMcpProvider`
  (label `Corkboard`), a static provider for the remote MCP server at
  `https://corkboard.wiki/mcp`. OAuth 2.1 discovery and consent are handled by
  the client.
- Bundles the vendored `skills/corkboard` CLI skill in the VSIX.
