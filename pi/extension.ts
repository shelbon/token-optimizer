import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createReadToolDefinition, createBashToolDefinition, getAgentDir } from "@earendil-works/pi-coding-agent";
import type { TextContent } from "@earendil-works/pi-ai";
import { fileURLToPath } from "node:url";
import { dirname, join, resolve } from "node:path";
import { mkdirSync } from "node:fs";
import { callBridge } from "./bridge.ts";

const packageRoot = dirname(dirname(fileURLToPath(import.meta.url)));
const HOT = 1200;
const LIFE = 5000;

type SessionState = { sessionId: string; model?: string; toolCalls: number; compactions: number; restored: boolean };

function textOf(content: unknown): string {
  if (!Array.isArray(content)) return "";
  return content.map((b: any) => b?.type === "text" && typeof b.text === "string" ? b.text : "").filter(Boolean).join("\n");
}
function replaceText(result: any, text: string): any {
  const old = Array.isArray(result.content) ? result.content : [];
  const nonText = old.filter((b: any) => b?.type !== "text");
  return { ...result, content: [{ type: "text", text } as TextContent, ...nonText] };
}
function prependWarning(result: any, warning?: string): any {
  if (!warning) return result;
  return { ...result, content: [{ type: "text", text: `[Token Optimizer] ${warning}\n\n` } as TextContent, ...(Array.isArray(result.content) ? result.content : [])] };
}
function sessionId(ctx: any, state: SessionState): string {
  return String(ctx?.sessionManager?.sessionId ?? ctx?.session?.id ?? state.sessionId ?? "unknown");
}
function showCommandOutput(ctx: any, message: string, type: "info" | "warning" | "error" = "info") {
  if (ctx?.ui?.notify) ctx.ui.notify(message, type);
  else process.stdout.write(`${message}\n`);
}

export default function tokenOptimizerPi(pi: ExtensionAPI) {
  const agentDir = getAgentDir();
  process.env.TOKEN_OPTIMIZER_RUNTIME = "pi";
  process.env.PI_CODING_AGENT_DIR = agentDir;
  process.env.TOKEN_OPTIMIZER_SNAPSHOT_DIR = join(agentDir, "token-optimizer", "data");
  mkdirSync(process.env.TOKEN_OPTIMIZER_SNAPSHOT_DIR, { recursive: true });
  let state: SessionState = { sessionId: "unknown", toolCalls: 0, compactions: 0, restored: false };
  let toolOverridesRegistered = false;

  const bridge = (cmd: string, payload: Record<string, unknown>, timeoutMs = HOT) => callBridge(packageRoot, cmd, { ...payload, session_id: state.sessionId }, { agentDir, timeoutMs });

  pi.on("session_start", async (event: any, ctx: any) => {
    state = { sessionId: String(ctx?.sessionManager?.sessionId ?? event?.sessionId ?? Date.now()), toolCalls: 0, compactions: 0, restored: false };
    registerBuiltinToolWrappers(pi, bridge, () => toolOverridesRegistered, (registered) => { toolOverridesRegistered = registered; });
    await bridge("session-start", { cwd: ctx?.cwd, reason: event?.reason }, LIFE).catch(() => undefined);
  });
  pi.on("model_select", (event: any) => { state.model = String(event?.model?.id ?? event?.model ?? state.model ?? "unknown"); });
  pi.on("before_agent_start", async (event: any, ctx: any) => {
    const opts = event?.systemPromptOptions ?? ctx?.getSystemPromptOptions?.();
    await bridge("prompt-start", { cwd: ctx?.cwd, model: state.model, systemPromptOptions: summarizePromptOptions(opts) }, LIFE).catch(() => undefined);
  });
  pi.on("message_end", () => undefined);
  pi.on("session_before_compact", async (event: any, ctx: any) => { await bridge("compact-before", { cwd: ctx?.cwd, reason: event?.reason, model: state.model }, LIFE).catch(() => undefined); });
  pi.on("session_compact", async (event: any, ctx: any) => { state.compactions += 1; await bridge("compact-after", { cwd: ctx?.cwd, result: event?.result }, LIFE).catch(() => undefined); });
  pi.on("session_shutdown", async (event: any, ctx: any) => { await bridge("session-end", { cwd: ctx?.cwd, reason: event?.reason, tool_calls: state.toolCalls, compactions: state.compactions }, LIFE).catch(() => undefined); });

  function registerBuiltinToolWrappers(
    piApi: ExtensionAPI,
    bridgeCall: typeof bridge,
    isRegistered: () => boolean,
    setRegistered: (registered: boolean) => void,
  ) {
    if (isRegistered()) return;
    setRegistered(true);

    const existingTools = safeGetAllTools(piApi);
    const canWrapRead = isBuiltinOrMissing(existingTools, "read");
    const canWrapBash = isBuiltinOrMissing(existingTools, "bash");

    // Only install execution wrappers when Pi is still using its built-in local tools.
    // If another extension already owns read/bash (SSH, containers, sandboxes, etc.),
    // leave that implementation in place and rely on tool_result hooks below so Token
    // Optimizer never reroutes execution back to the host by constructing fresh built-ins.
    if (canWrapRead) {
      const readTool = createReadToolDefinition(process.cwd());
      piApi.registerTool({
        ...readTool,
        async execute(id: string, params: any, signal?: AbortSignal, onUpdate?: any, ctx?: any) {
          const cwd = String(ctx?.cwd ?? process.cwd());
          const cwdReadTool = createReadToolDefinition(cwd);
          const requestedPath = String(params.path);
          const normalizedPath = requestedPath.startsWith("@") ? requestedPath.slice(1) : requestedPath;
          const abs = resolve(cwd, normalizedPath);
          const pre = await bridgeCall("read-before", { path: abs, cwd: ctx?.cwd, offset: params.offset ?? 0, limit: params.limit ?? 0 }, HOT).catch(() => undefined);
          if (pre?.action === "replace" && typeof pre.content === "string") return { content: [{ type: "text", text: pre.content }], details: { tokenOptimizer: pre } } as any;
          const result = await cwdReadTool.execute(id, params, signal, onUpdate, ctx);
          return prependWarning(result, typeof pre?.warning === "string" ? pre.warning : undefined);
        }
      });
    }

    if (canWrapBash) {
      const bashTool = createBashToolDefinition(process.cwd());
      piApi.registerTool({
        ...bashTool,
        async execute(id: string, params: any, signal?: AbortSignal, onUpdate?: any, ctx?: any) {
          const cwd = String(ctx?.cwd ?? process.cwd());
          const cwdBashTool = createBashToolDefinition(cwd);
          const result: any = await cwdBashTool.execute(id, params, signal, onUpdate, ctx);
          const rc = (result.details as any)?.exitCode ?? (result.details as any)?.code ?? 0;
          if (result.isError || rc !== 0) return result;
          const response = await bridgeCall("bash-result", { command: params.command, text: textOf(result.content), returncode: rc, is_error: result.isError }, LIFE).catch(() => undefined);
          if (response?.action === "replace" && typeof response.content === "string") return replaceText(result, response.content);
          return result;
        }
      });
    }
  }

  pi.on("tool_result", async (event: any, ctx: any) => {
    state.toolCalls += 1;
    if (!event.isError && ["edit", "write"].includes(String(event.toolName))) {
      const p = (event.input as any)?.path ?? (event.input as any)?.file_path;
      if (typeof p === "string") await bridge("read-invalidate", { path: p, cwd: ctx?.cwd }, HOT).catch(() => undefined);
    }
    if (["read", "bash"].includes(String(event.toolName))) return undefined;
    if (event.isError) return undefined;
    const response = await bridge("tool-result", { tool_name: event.toolName, text: textOf(event.content), is_error: event.isError }, LIFE).catch(() => undefined);
    if (response?.action === "replace" && typeof response.content === "string") return { content: [{ type: "text", text: response.content }, ...(Array.isArray(event.content) ? event.content.filter((b: any) => b?.type !== "text") : [])], details: event.details, isError: false };
    return undefined;
  });

  pi.registerCommand("token-optimizer", { description: "Start the Pi Token Optimizer skill", handler: async (args: string) => { pi.sendUserMessage(`/skill:token-optimizer ${args ?? ""}`.trim()); } });
  pi.registerCommand("token-status", { description: "Show Token Optimizer Pi status", handler: async (_args: string, ctx: any) => { const r = await bridge("status", { cwd: ctx?.cwd, model: state.model }, LIFE); showCommandOutput(ctx, formatStatus(r, state)); } });
  pi.registerCommand("token-doctor", { description: "Run Token Optimizer Pi diagnostics", handler: async (_args: string, ctx: any) => { const r = await bridge("doctor", { cwd: ctx?.cwd, overrides: { read: true, bash: true } }, LIFE); showCommandOutput(ctx, formatDoctor(r)); } });
  pi.registerCommand("token-dashboard", { description: "Show Token Optimizer dashboard information", handler: async (_args: string, ctx: any) => { const r = await bridge("dashboard", { cwd: ctx?.cwd }, LIFE); showCommandOutput(ctx, `Token Optimizer dashboard data directory: ${(r as any)?.data_dir ?? process.env.TOKEN_OPTIMIZER_SNAPSHOT_DIR}\nNo unmanaged server was started by the Pi extension.`); } });
}

function safeGetAllTools(pi: ExtensionAPI): any[] {
  try {
    const tools = pi.getAllTools?.();
    return Array.isArray(tools) ? tools : [];
  } catch {
    return [];
  }
}
function isBuiltinOrMissing(tools: any[], name: string): boolean {
  return !tools.some((t) => {
    if (t?.name !== name) return false;
    const source = t?.sourceInfo?.source;
    return source !== undefined && source !== null && source !== "builtin";
  });
}

function summarizePromptOptions(opts: any) {
  const size = (v: any) => typeof v === "string" ? v.length : Buffer.byteLength(JSON.stringify(v ?? ""));
  return { cwd: opts?.cwd, activeTools: opts?.activeTools, contextFiles: (opts?.contextFiles ?? []).map((f: any) => ({ path: f.path, bytes: size(f.content), preview: String(f.content ?? "").slice(0, 200) })), skills: (opts?.skills ?? []).map((s: any) => ({ name: s.name, description: s.description, bytes: size(s.content) })), appendedSystemPromptBytes: size(opts?.appendedSystemPrompt), totalSystemPromptBytes: size(opts) };
}
function formatStatus(r: any, s: SessionState): string { const x = r?.session ?? {}; return `Token Optimizer Pi status\n- Session: ${x.session_id ?? s.sessionId}\n- Model: ${x.model ?? s.model ?? "unknown"}\n- Context usage: input ${x.total_input_tokens ?? 0}, output ${x.total_output_tokens ?? 0}, cache read ${x.cache_read_tokens ?? 0}, cache write ${x.cache_creation_tokens ?? 0} tokens${x.estimated ? " (estimated)" : ""}\n- Cost: $${Number(x.total_cost_usd ?? 0).toFixed(4)}\n- Tool calls: ${x.tool_call_count ?? s.toolCalls}\n- Compactions: ${x.compaction_count ?? s.compactions}\n- Data: ${r?.data_dir ?? "unknown"}`; }
function formatDoctor(r: any): string { const c = r?.checks ?? {}; return `Token Optimizer Pi doctor\n- Python: ${c.python ?? "unavailable"}\n- Runtime: ${c.runtime ?? "unknown"} (${c.runtime_human ?? "unknown"})\n- Pi agent dir: ${c.pi_agent_dir ?? "unknown"}\n- Data dir: ${c.data_dir ?? "unknown"}\n- Bridge: ${c.bridge ?? "missing"}\n- Read override: active\n- Bash override: active\n- Claude isolation: ${c.claude_confined ? "pass" : "FAILED"}`; }
