# Pi support (beta)

Token Optimizer can be installed as a native Pi package. It uses Pi's TypeScript extension API for commands, lifecycle hooks, and read/bash tool overrides, plus a bounded Python JSON bridge to reuse the existing optimization engine.

## Installation

```bash
pi install https://github.com/shelbon/token-optimizer
```

## Local development

```bash
git clone https://github.com/shelbon/token-optimizer.git
cd token-optimizer
pi -e .
```

## Local smoke test

Use a clean checkout so Pi loads the same files that will ship in the package:

```bash
git clone https://github.com/shelbon/token-optimizer.git
cd token-optimizer
pi -e .
```

Inside Pi, run the built-in diagnostics first:

```text
/token-doctor
/token-status
```

Then exercise the integration paths that have regressed before:

1. Ask Pi to read a small file, for example `README.md`, then edit that same file. The read-cache invalidation path should run without errors.
2. Run a short shell command, for example `printf 'token-optimizer pi smoke test\n'`, and confirm the command completes normally.
3. Run `/token-status` again and confirm the tool-call count or token totals changed.
4. Confirm Pi data was written under the Pi agent directory reported by `/token-doctor`, not under `~/.claude`.

To test an installed Git package instead of editable mode, uninstall any editable copy and install from your branch or fork URL:

```bash
pi remove https://github.com/shelbon/token-optimizer
pi install https://github.com/<your-user>/token-optimizer
```

## Removal

```bash
pi remove https://github.com/shelbon/token-optimizer
```

## Commands

- `/token-optimizer` starts the Pi-specific skill workflow.
- `/token-status` shows current session usage, compactions, tool calls, and savings when available.
- `/token-doctor` checks Python, bridge paths, data directories, override activation, and Claude isolation.
- `/token-dashboard` reports local dashboard data without remote telemetry.

## Data and isolation

All Pi data is stored under `<Pi agent dir>/token-optimizer/`. When Pi runtime is active, Token Optimizer must not inspect, create, or mutate `~/.claude`.

## Known limitations

Pi support is beta. Default Pi compaction is retained; Token Optimizer records checkpoints before/after compaction, but dynamic compaction-instruction enrichment is partial until a safe custom compactor is added behind an opt-in flag.
