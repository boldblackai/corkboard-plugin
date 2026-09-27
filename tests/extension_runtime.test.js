"use strict";
/**
 * Runtime test for the compiled extension entry point (out/extension.js).
 *
 * The extension only uses the `vscode` host API, which does not exist outside
 * a VS Code process, so this test stubs that module and loads the real build.
 * Proves that activation registers the static Corkboard MCP server definition
 * provider and that the definition points at the hosted MCP endpoint.
 *
 * Stdlib + node only; run directly:  node tests/extension_runtime.test.js
 */

const Module = require("node:module");
const path = require("node:path");

const REPO = path.resolve(__dirname, "..");
const failures = [];

function check(name, condition, detail) {
  if (condition) {
    console.log(`ok   ${name}`);
  } else {
    console.log(`FAIL ${name}${detail === undefined ? "" : " :: " + detail}`);
    failures.push(name);
  }
}

// --- stub the `vscode` host module ----------------------------------------
const registered = [];

class Uri {
  constructor(value) {
    this.value = value;
  }
  static parse(value) {
    return new Uri(value);
  }
  toString() {
    return this.value;
  }
}

class McpHttpServerDefinition {
  constructor(label, uri, headers) {
    this.label = label;
    this.uri = uri;
    this.headers = headers;
  }
}

const vscodeStub = {
  Uri,
  McpHttpServerDefinition,
  lm: {
    registerMcpServerDefinitionProvider(id, provider) {
      registered.push({ id, provider });
      return { dispose() {} };
    },
  },
};

const realLoad = Module._load;
Module._load = function (request, ...rest) {
  if (request === "vscode") {
    return vscodeStub;
  }
  return realLoad.call(this, request, ...rest);
};

const extension = require(path.join(REPO, "out", "extension.js"));

// --- activation -----------------------------------------------------------
const subscriptions = [];
extension.activate({ subscriptions });

check("activation registers exactly one provider", registered.length === 1,
  `registered=${JSON.stringify(registered.map((r) => r.id))}`);
const registration = registered[0] || {};
check("provider id is corkboardMcpProvider", registration.id === "corkboardMcpProvider",
  String(registration.id));
check("provider is disposed with the extension context", subscriptions.length === 1,
  `subscriptions=${subscriptions.length}`);
check("deactivate() is exported", typeof extension.deactivate === "function");

// --- the MCP server definition --------------------------------------------
const provider = registration.provider || {};
check("provider implements provideMcpServerDefinitions",
  typeof provider.provideMcpServerDefinitions === "function");

const definitions = provider.provideMcpServerDefinitions
  ? provider.provideMcpServerDefinitions()
  : [];
check("exactly one MCP server definition", definitions.length === 1,
  `count=${definitions.length}`);

const definition = definitions[0] || {};
check("definition is a vscode.McpHttpServerDefinition",
  definition instanceof McpHttpServerDefinition);
check("definition label is Corkboard", definition.label === "Corkboard",
  String(definition.label));
check("definition uri is https://corkboard.wiki/mcp",
  String(definition.uri) === "https://corkboard.wiki/mcp", String(definition.uri));
check("definition carries no credentials", definition.headers === undefined,
  JSON.stringify(definition.headers));

check("resolveMcpServerDefinition returns the definition unchanged",
  typeof provider.resolveMcpServerDefinition === "function" &&
    provider.resolveMcpServerDefinition(definition) === definition);

if (failures.length) {
  console.error(`FAILED: ${failures.length} check(s): ${failures.join(", ")}`);
  process.exit(1);
}
console.log("ALL EXTENSION RUNTIME CHECKS PASSED");
