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
