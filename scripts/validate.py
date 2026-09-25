#!/usr/bin/env python3
"""Validate corkboard-plugin manifests, MCP configs, and skill structure.

Exits non-zero on any error. Run locally and in CI.
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ERRORS: list[str] = []

PLUGIN_NAME_RE = re.compile(r"^[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def err(msg: str) -> None:
    ERRORS.append(msg)


def load_json(rel: str) -> dict:
    path = ROOT / rel
    if not path.is_file():
        err(f"missing {rel}")
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        err(f"{rel}: invalid JSON: {e}")
        return {}


def main() -> int:
    # --- Agent Plugins (open standard) manifest -------------------------
    agent = load_json("plugin.json")
    if agent:
        if agent.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json":
            err("plugin.json: $schema must point at agent-plugins.org 1.0.0")
        name = agent.get("name", "")
        if not PLUGIN_NAME_RE.match(name) or "--" in name or ".." in name:
            err(f"plugin.json: invalid name {name!r}")
        for field in ("description", "license", "homepage", "repository"):
            if field in agent and not isinstance(agent[field], str):
                err(f"plugin.json: {field} must be a string")
        if "keywords" in agent and not (
            isinstance(agent["keywords"], list)
            and all(isinstance(k, str) for k in agent["keywords"])
        ):
            err("plugin.json: keywords must be a list of strings")
        if "author" in agent:
            a = agent["author"]
            if not isinstance(a, dict) or set(a) - {"name", "email", "url"}:
                err("plugin.json: author must be an object with name/email/url only")

    # --- Agent Plugins mcp.json ----------------------------------------
    mcp = load_json("mcp.json")
    if mcp:
        if mcp.get("$schema") != "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json":
            err("mcp.json: $schema must point at agent-plugins.org 1.0.0")
        servers = mcp.get("mcpServers")
        if not isinstance(servers, dict) or not servers:
            err("mcp.json: mcpServers must be a non-empty object")
        else:
            for sname, srv in servers.items():
                if not isinstance(srv, dict):
                    err(f"mcp.json: server {sname} must be an object")
                    continue
                stype = srv.get("type")
                if stype == "streamable-http":
                    url = srv.get("url", "")
                    if not url.startswith("https://"):
                        err(f"mcp.json: {sname}: non-loopback URL must be HTTPS")
                elif stype == "stdio":
                    if "command" not in srv:
                        err(f"mcp.json: {sname}: stdio server needs command")
                else:
                    err(f"mcp.json: {sname}: unknown transport {stype!r}")
                for banned in ("Authorization", "X-Api-Key"):
                    if banned in srv.get("headers", {}):
                        err(f"mcp.json: {sname}: credential header in package data")

    # --- Cursor manifest -------------------------------------------------
    cursor = load_json(".cursor-plugin/plugin.json")
    if cursor:
        cname = cursor.get("name", "")
        if not PLUGIN_NAME_RE.match(cname):
            err(f".cursor-plugin/plugin.json: invalid name {cname!r}")
        for field in ("displayName", "description", "version", "license"):
            if field not in cursor:
                err(f".cursor-plugin/plugin.json: missing {field}")
        logo = cursor.get("logo", "")
        if logo and (logo.startswith("/") or ".." in logo):
            err(".cursor-plugin/plugin.json: logo must be a relative path")
        elif logo and not (ROOT / logo).is_file():
            err(f".cursor-plugin/plugin.json: logo file missing: {logo}")
        ref = cursor.get("mcpServers")
        if isinstance(ref, str):
            ref_path = ROOT / ref.removeprefix("./")
            if not ref_path.is_file():
                err(f".cursor-plugin/plugin.json: mcpServers file missing: {ref}")
            else:
                cm = json.loads(ref_path.read_text())
                servers = cm.get("mcpServers", {})
                if "corkboard" not in servers:
                    err(f"{ref}: expected a 'corkboard' server entry")

    # --- Skill frontmatter ----------------------------------------------
    skill_md = ROOT / "skills" / "corkboard" / "SKILL.md"
    if not skill_md.is_file():
        err("skills/corkboard/SKILL.md missing")
    else:
        text = skill_md.read_text()
        if not text.startswith("---\n"):
            err("SKILL.md: missing YAML frontmatter")
        else:
            end = text.find("\n---", 4)
            fm = text[4:end]
            has_name = re.search(r"^name:\s*\S", fm, re.M)
            has_desc = re.search(r"^description:\s*\S", fm, re.M)
            if not has_name:
                err("SKILL.md: frontmatter needs name")
            elif not SKILL_NAME_RE.match(has_name.group(0).split(":", 1)[1].strip().strip('"')):
                err("SKILL.md: name must be lowercase kebab-case")
            if not has_desc:
                err("SKILL.md: frontmatter needs description")
        entry = ROOT / "skills" / "corkboard" / "script" / "corkboard.py"
        if not entry.is_file():
            err("skills/corkboard/script/corkboard.py missing")

    # --- report ----------------------------------------------------------
    if ERRORS:
        for e in ERRORS:
            print(f"ERROR: {e}", file=sys.stderr)
        return 1
    print("OK: manifests, MCP configs, and skill structure valid")
    return 0


if __name__ == "__main__":
    sys.exit(main())
