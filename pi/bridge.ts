import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { join } from "node:path";

export type BridgeResponse = { ok?: boolean; action?: string; content?: string; warning?: string; [key: string]: unknown };

let cachedPython: string[] | undefined;

async function tryPython(cmd: string[]): Promise<boolean> {
  return new Promise((resolve) => {
    const p = spawn(cmd[0]!, [...cmd.slice(1), "--version"], { shell: false, stdio: "ignore" });
    p.on("error", () => resolve(false));
    p.on("exit", (code) => resolve(code === 0));
  });
}

export async function findPython(): Promise<string[] | undefined> {
  if (cachedPython) return cachedPython;
  const override = process.env.TOKEN_OPTIMIZER_PYTHON;
  const candidates = override ? [[override]] : [["python3"], ["python"], ["py", "-3"]];
  for (const c of candidates) {
    if (await tryPython(c)) return (cachedPython = c);
  }
  return undefined;
}

export async function callBridge(packageRoot: string, command: string, payload: Record<string, unknown>, opts: { agentDir: string; timeoutMs?: number } ): Promise<BridgeResponse | undefined> {
  const script = join(packageRoot, "plugins", "token-optimizer", "skills", "token-optimizer", "scripts", "pi_bridge.py");
  if (!existsSync(script)) return undefined;
  const py = await findPython();
  if (!py) return undefined;
  const input = JSON.stringify(payload);
  if (Buffer.byteLength(input) > 2 * 1024 * 1024) return undefined;
  return new Promise((resolve) => {
    const child = spawn(py[0]!, [...py.slice(1), script, command], {
      shell: false,
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, TOKEN_OPTIMIZER_RUNTIME: "pi", PI_CODING_AGENT_DIR: opts.agentDir, TOKEN_OPTIMIZER_SNAPSHOT_DIR: join(opts.agentDir, "token-optimizer", "data") },
    });
    let stdout = ""; let stderr = ""; let done = false;
    const finish = (value?: BridgeResponse) => { if (done) return; done = true; clearTimeout(timer); resolve(value); };
    const timer = setTimeout(() => { child.kill("SIGKILL"); finish(undefined); }, opts.timeoutMs ?? 1200);
    child.stdout.on("data", (d) => { stdout += d; if (stdout.length > 1024 * 1024) { child.kill("SIGKILL"); finish(undefined); } });
    child.stderr.on("data", (d) => { stderr += d; if (stderr.length > 128 * 1024) child.kill("SIGKILL"); });
    child.on("error", () => finish(undefined));
    child.on("exit", () => {
      if (done) return;
      try {
        const parsed = JSON.parse(stdout.trim());
        finish(parsed && typeof parsed === "object" ? parsed as BridgeResponse : undefined);
      } catch { finish(undefined); }
    });
    child.stdin.end(input);
  });
}
