import * as vscode from "vscode";

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
export function activate(context: vscode.ExtensionContext): void {
  const provider: vscode.McpServerDefinitionProvider<vscode.McpServerDefinition> =
    {
      provideMcpServerDefinitions(): vscode.McpServerDefinition[] {
        return [
          new vscode.McpHttpServerDefinition(
            "Corkboard",
            vscode.Uri.parse(CORKBOARD_MCP_URI),
          ),
        ];
      },
      resolveMcpServerDefinition(
        server: vscode.McpServerDefinition,
      ): vscode.McpServerDefinition {
        return server;
      },
    };

  context.subscriptions.push(
    vscode.lm.registerMcpServerDefinitionProvider(
      "corkboardMcpProvider",
      provider,
    ),
  );
}

export function deactivate(): void {
  // Nothing to dispose: the provider registration is a context subscription.
}
