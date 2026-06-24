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
