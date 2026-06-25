#!/usr/bin/env python3
"""Stable JSON bridge for the native Pi Token Optimizer extension."""
from __future__ import annotations

import hashlib, json, os, sys, time
from pathlib import Path
from typing import Any

MAX_STDIN = 2 * 1024 * 1024
MAX_TEXT = 5 * 1024 * 1024

from plugin_env import resolve_snapshot_dir
from runtime_env import detect_runtime, pi_home, runtime_home, runtime_name_for_humans
from session_store import _sanitize_session_id


def _emit(obj: dict[str, Any], code: int = 0) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    raise SystemExit(code)


def _read() -> dict[str, Any]:
    data = sys.stdin.buffer.read(MAX_STDIN + 1)
    if not data:
        _emit({"ok": False, "error": "empty stdin", "action": "allow"})
    if len(data) > MAX_STDIN:
        _emit({"ok": False, "error": "payload too large", "action": "allow"})
    try:
        obj = json.loads(data.decode("utf-8"))
    except Exception as e:
        print(f"pi_bridge malformed JSON: {e}", file=sys.stderr)
        _emit({"ok": False, "error": "malformed json", "action": "allow"})
    if not isinstance(obj, dict):
        _emit({"ok": False, "error": "request must be object", "action": "allow"})
    return obj


def _data_dir() -> Path:
    d = resolve_snapshot_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_path(raw: Any, cwd: Any = None) -> Path | None:
    if not isinstance(raw, str) or not raw:
        return None
    base = Path(cwd).expanduser() if isinstance(cwd, str) and cwd else Path.cwd()
    p = Path(raw).expanduser()
    if not p.is_absolute(): p = base / p
    try: return p.resolve(strict=False)
    except OSError: return None


def _cache_file(session_id: str) -> Path:
    d = _data_dir() / "pi-read-cache"
    d.mkdir(parents=True, exist_ok=True)
    return d / f"{_sanitize_session_id(session_id or 'unknown')}.json"


def _load_cache(session_id: str) -> dict[str, Any]:
    try:
        p = _cache_file(session_id)
        if p.is_file() and p.stat().st_size < 1_000_000:
            data = json.loads(p.read_text())
            return data if isinstance(data, dict) else {}
    except Exception: pass
    return {}


def _save_cache(session_id: str, data: dict[str, Any]) -> None:
    p = _cache_file(session_id); tmp = p.with_suffix('.tmp')
    tmp.write_text(json.dumps(data)); os.chmod(tmp, 0o600); tmp.replace(p)


def _archive(prefix: str, session_id: str, text: str) -> str | None:
    """Archive Pi-replaced content in the shared expandable JSON format."""
    if len(text) > MAX_TEXT:
        text = text[:MAX_TEXT] + "\n[Token Optimizer: archived payload truncated at 5 MiB]"
    key = f"pi-{prefix}-{hashlib.sha256(text.encode('utf-8','replace')).hexdigest()[:16]}"
    try:
        from archive_result import archive_original
        return archive_original(text, session_id or "unknown", key, f"Pi {prefix.title()}")
    except Exception:
        return None


def read_before(req: dict[str, Any]) -> dict[str, Any]:
    path = _safe_path(req.get('path') or req.get('file_path'), req.get('cwd'))
    if path is None or req.get('offset') or req.get('limit'):
        return {"ok": True, "action": "allow"}
    sid = str(req.get('session_id') or 'unknown')
    cache = _load_cache(sid)
    try:
        st = path.stat(); fp = str(path); sig = {"mtime": st.st_mtime_ns, "size": st.st_size}
    except OSError:
        return {"ok": True, "action": "allow"}
    prev = cache.get(fp)
    cache[fp] = sig; _save_cache(sid, cache)
    if prev == sig and st.st_size >= 4096:
        if st.st_size > MAX_TEXT:
            return {"ok": True, "action": "allow"}
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return {"ok": True, "action": "allow"}
        key = _archive('read', sid, content)
        if key is None:
            return {"ok": True, "action": "allow"}
        return {"ok": True, "action": "replace", "replacement_type": "reason", "archive_key": key, "tokens_saved": max(0, st.st_size // 4 - 80), "content": f"[Token Optimizer] Repeated full-file read skipped for `{fp}` ({st.st_size:,} bytes). File is unchanged from the previous read in this session. Use a ranged read or ask to expand `{key}` if exact content is needed."}
    return {"ok": True, "action": "allow"}


def read_invalidate(req: dict[str, Any]) -> dict[str, Any]:
    path = _safe_path(req.get('path') or req.get('file_path'), req.get('cwd'))
    sid = str(req.get('session_id') or 'unknown')
    if path:
        cache = _load_cache(sid); cache.pop(str(path), None); _save_cache(sid, cache)
    return {"ok": True, "action": "invalidated"}


def bash_result(req: dict[str, Any]) -> dict[str, Any]:
    if req.get('returncode') not in (0, None) or req.get('is_error'):
        return {"ok": True, "action": "allow"}
    text = str(req.get('text') or '')[:MAX_TEXT]
    if len(text) < 100: return {"ok": True, "action": "allow"}
    from bash_compress import compress
    compressed = compress(str(req.get('command') or ''), text, 0, str(req.get('stderr') or ''))
    if not isinstance(compressed, str) or len(compressed) >= len(text) * 0.9:
        return {"ok": True, "action": "allow"}
    key = _archive('bash', str(req.get('session_id') or 'unknown'), text)
    if key is None:
        return {"ok": True, "action": "allow"}
    pointer = f"\n\n[Token Optimizer archived original output: expand {key}]"
    return {"ok": True, "action": "replace", "content": compressed + pointer, "archive_key": key, "tokens_saved": max(0, (len(text)-len(compressed))//4)}


def _session_summary(parsed: dict[str, Any]) -> dict[str, Any]:
    """Return only fields needed by Pi command formatters.

    Full Pi session parses include turn text, active branch entries, and tool
    result payloads, which can easily exceed the TypeScript bridge stdout cap.
    """
    keys = (
        "session_id", "cwd", "model", "thinking_level", "tool_call_count",
        "compaction_count", "compactions", "duration_minutes", "message_count",
        "api_calls", "total_input_tokens", "total_output_tokens",
        "total_cache_read", "total_cache_create", "cache_read_tokens",
        "cache_creation_tokens", "cache_hit_rate", "model_usage",
        "model_usage_breakdown", "skills_used", "subagents_used", "version",
        "topic", "first_ts", "total_cost_usd", "estimated", "filepath",
    )
    return {key: parsed.get(key) for key in keys if key in parsed}


def status(req: dict[str, Any]) -> dict[str, Any]:
    import pi_session
    fp = pi_session.find_current_session_jsonl()
    parsed = pi_session.parse_session_jsonl(fp) if fp else {}
    return {"ok": True, "action": "status", "runtime": detect_runtime(), "session": _session_summary(parsed), "data_dir": str(_data_dir())}


def doctor(req: dict[str, Any]) -> dict[str, Any]:
    agent = pi_home(); data = _data_dir(); claude = Path.home()/'.claude'
    checks = {"python": sys.version.split()[0], "runtime": detect_runtime(), "runtime_human": runtime_name_for_humans(), "pi_agent_dir": str(agent), "data_dir": str(data), "bridge": str(Path(__file__).resolve()), "claude_confined": not str(data.resolve(strict=False)).startswith(str(claude.resolve(strict=False)))}
    return {"ok": True, "action": "doctor", "checks": checks}


def lifecycle(name: str, req: dict[str, Any]) -> dict[str, Any]:
    d = _data_dir() / ("context-snapshots" if 'prompt' in name else "sessions")
    d.mkdir(parents=True, exist_ok=True)
    sid = _sanitize_session_id(str(req.get('session_id') or 'unknown'))
    payload = {"event": name, "time": time.time(), "request": req}
    p = d / f"{sid}-{name}.json"
    p.write_text(json.dumps(payload, default=str)[:MAX_TEXT]); os.chmod(p, 0o600)
    return {"ok": True, "action": "recorded"}


COMMANDS = {"read-before": read_before, "read-invalidate": read_invalidate, "bash-result": bash_result, "tool-result": bash_result, "status": status, "doctor": doctor, "dashboard": status}

if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else ''
    req = _read()
    try:
        if cmd in COMMANDS: _emit(COMMANDS[cmd](req))
        if cmd in {"prompt-start","compact-before","compact-after","session-end","session-start"}: _emit(lifecycle(cmd, req))
        _emit({"ok": False, "error": "unknown command", "action": "allow"})
    except SystemExit: raise
    except Exception as e:
        print(f"pi_bridge {cmd} failed: {e}", file=sys.stderr)
        _emit({"ok": False, "error": "bridge failed", "action": "allow"})
