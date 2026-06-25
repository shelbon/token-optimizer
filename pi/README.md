# Token Optimizer for Pi (Beta)

This package adds native Pi coding-agent integration through a TypeScript extension and a small Python JSON bridge. Host orchestration stays in TypeScript; compression, caching, session parsing, checkpointing, and reporting reuse the existing Python engine.

## Install

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

## Remove

Pi package documentation uses the install source as the remove identity for Git packages:

```bash
pi remove https://github.com/shelbon/token-optimizer
```

## Data directories

All Pi data is beneath `<Pi agent dir>/token-optimizer/`:

- `config.json`
- `data/sessions/`
- `data/tool-archive/`
- `data/context-snapshots/`
- `data/pi-read-cache/`

The extension obtains the agent directory with Pi's `getAgentDir()` and exports `TOKEN_OPTIMIZER_RUNTIME=pi`, `PI_CODING_AGENT_DIR`, and `TOKEN_OPTIMIZER_SNAPSHOT_DIR` to Python.

## Limitations

Pi support is beta. Token Optimizer does not replace Pi's default compactor yet; pre/post compaction checkpoints are recorded and dynamic compaction-instruction enrichment is documented as follow-up work.
