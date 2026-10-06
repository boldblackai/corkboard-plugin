"use strict";
Object.defineProperty(exports, "__esModule", { value: true });
exports.activate = activate;
exports.deactivate = deactivate;
const vscode = require("vscode");
/** Remote Corkboard MCP endpoint (Streamable HTTP, OAuth 2.1 discovery). */
const CORKBOARD_MCP_URI = "https://corkboard.wiki/mcp";
/**
 * Register the static Corkboard MCP server definition.
 *
 * The provider has no dynamic behaviour: it returns one
 * {@link vscode.McpHttpServerDefinition} for the hosted Corkboard MCP server.
 * OAuth 2.1 discovery, consent, and token refresh are handled client-side by
 * VS Code / Cursor, so no credentials pass through this extension.
 */
function activate(context) {
    const provider = {
        provideMcpServerDefinitions() {
            return [
                new vscode.McpHttpServerDefinition("Corkboard", vscode.Uri.parse(CORKBOARD_MCP_URI)),
            ];
        },
        resolveMcpServerDefinition(server) {
            return server;
        },
    };
    context.subscriptions.push(vscode.lm.registerMcpServerDefinitionProvider("corkboardMcpProvider", provider));
}
function deactivate() {
    // Nothing to dispose: the provider registration is a context subscription.
}
