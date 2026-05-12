import * as vscode from "vscode";

export function activate(context: vscode.ExtensionContext): void {
  const cli = () =>
    vscode.workspace.getConfiguration("qx").get<string>("cliExecutable", "qx");

  const run = (args: string) => {
    const terminal =
      vscode.window.activeTerminal ?? vscode.window.createTerminal("qx");
    terminal.show();
    terminal.sendText(`${cli()} ${args}`);
  };

  const prompt = async (placeholder: string): Promise<string | undefined> =>
    vscode.window.showInputBox({ placeHolder: placeholder, ignoreFocusOut: true });

  const pickMethod = async (): Promise<string | undefined> =>
    vscode.window.showQuickPick(["GET", "POST", "PUT", "PATCH", "DELETE"], {
      placeHolder: "HTTP method",
    });

  context.subscriptions.push(
    vscode.commands.registerCommand("qx.newService", async () => {
      const name = await prompt("Service name (kebab-case, e.g. order-service)");
      if (name) run(`new service ${name}`);
    }),

    vscode.commands.registerCommand("qx.generateAggregate", async () => {
      const name = await prompt("Aggregate name (PascalCase, e.g. Order)");
      if (name) run(`generate aggregate ${name}`);
    }),

    vscode.commands.registerCommand("qx.generateCommand", async () => {
      const name = await prompt("Command name (PascalCase, e.g. CreateOrder)");
      if (!name) return;
      const agg = await prompt("Target aggregate (optional, e.g. Order)");
      run(`generate command ${name}${agg ? ` --aggregate ${agg}` : ""}`);
    }),

    vscode.commands.registerCommand("qx.generateQuery", async () => {
      const name = await prompt("Query name (PascalCase, e.g. GetOrder)");
      if (name) run(`generate query ${name}`);
    }),

    vscode.commands.registerCommand("qx.generateEvent", async () => {
      const name = await prompt("Event name (PascalCase, e.g. OrderPlaced)");
      if (name) run(`generate event ${name}`);
    }),

    vscode.commands.registerCommand("qx.generateEndpoint", async () => {
      const path = await prompt("Route path (e.g. /orders)");
      if (!path) return;
      const handler = await prompt("Handler name (e.g. CreateOrder)");
      if (!handler) return;
      const method = (await pickMethod()) ?? "POST";
      run(`generate endpoint ${path} --handler ${handler} --method ${method}`);
    }),

    vscode.commands.registerCommand("qx.devUp", () => run("dev up")),
    vscode.commands.registerCommand("qx.devDown", () => run("dev down")),
    vscode.commands.registerCommand("qx.devLogs", async () => {
      const svc = await prompt("Service name to tail (leave empty for all)");
      run(`dev logs${svc ? ` ${svc}` : ""}`);
    }),

    vscode.commands.registerCommand("qx.doctor", () => run("doctor --connectivity"))
  );
}

export function deactivate(): void {}
