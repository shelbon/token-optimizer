#!/usr/bin/env python3
"""Pi session JSONL adapter for Token Optimizer."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from runtime_env import pi_home

MAX_PARSE_FILE_BYTES = 96 * 1024 * 1024
MAX_JSONL_LINE_CHARS = 8 * 1024 * 1024
CHARS_PER_TOKEN = 4


def _sessions_dir() -> Path:
    return pi_home() / "sessions"


def is_pi_session_path(filepath: str | Path | None) -> bool:
    if not filepath:
        return False
    try:
        return Path(filepath).resolve(strict=False).is_relative_to(_sessions_dir().resolve(strict=False))
    except (OSError, ValueError):
        return False


def _safe_int(v: Any) -> int:
    try:
        return max(0, int(v or 0))
    except (TypeError, ValueError):
        return 0


def _safe_float(v: Any) -> float:
    try:
        return float(v or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _parse_ts(v: Any) -> datetime | None:
    try:
        if isinstance(v, (int, float)):
            return datetime.fromtimestamp(v, tz=timezone.utc)
        if isinstance(v, str) and v:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
    except (OSError, ValueError, TypeError):
        return None
    return None


def _iter_records(path: Path):
    try:
        if not path.is_file() or path.stat().st_size > MAX_PARSE_FILE_BYTES:
            return
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if len(line) > MAX_JSONL_LINE_CHARS:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(rec, dict):
                    yield rec
    except OSError:
        return


def find_all_jsonl_files(days: int = 30):
    root = _sessions_dir()
    if not root.is_dir():
        return []
    out = []
    for p in root.rglob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        out.append((str(p), st.st_mtime, p.name))
    out.sort(key=lambda x: x[1], reverse=True)
    return out


def find_current_session_jsonl():
    files = find_all_jsonl_files(days=3650)
    return files[0][0] if files else None


def find_session_jsonl_by_id(session_id: str):
    safe = "".join(ch for ch in str(session_id) if ch.isalnum() or ch in "._-")[:120]
    if not safe:
        return None
    for fp, _m, _n in find_all_jsonl_files(days=3650):
        if safe in Path(fp).stem:
            return fp
        header = next(_iter_records(Path(fp)), None)
        if isinstance(header, dict) and str(header.get("sessionId") or header.get("id") or "") == safe:
            return fp
    return None


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _usage(d: dict[str, Any]) -> dict[str, int | float]:
    u = d.get("usage") if isinstance(d.get("usage"), dict) else d
    return {
        "input_tokens": _safe_int(u.get("input") or u.get("inputTokens") or u.get("promptTokens")),
        "output_tokens": _safe_int(u.get("output") or u.get("outputTokens") or u.get("completionTokens")),
        "cache_read_tokens": _safe_int(u.get("cacheRead") or u.get("cacheReadTokens")),
        "cache_creation_tokens": _safe_int(u.get("cacheWrite") or u.get("cacheWriteTokens")),
        "cost_usd": _safe_float(u.get("cost") or u.get("totalCost") or u.get("costUsd")),
    }


def parse_session_jsonl(filepath):
    path = Path(filepath)
    entries = list(_iter_records(path) or [])
    header = entries[0] if entries else {}
    session_id = str(header.get("sessionId") or header.get("id") or path.stem)
    cwd = str(header.get("cwd") or header.get("projectCwd") or "")
    nodes: dict[str, dict[str, Any]] = {}
    active_id = None
    turns = []
    tool_calls = []
    tool_results: dict[str, Any] = {}
    seen_usage: set[str] = set()
    total = {"input_tokens": 0, "output_tokens": 0, "cache_read_tokens": 0, "cache_creation_tokens": 0, "cost_usd": 0.0}
    compactions = 0
    model = None
    thinking_level = None
    for i, rec in enumerate(entries):
        typ = str(rec.get("type") or rec.get("entryType") or rec.get("kind") or "")
        if typ == "message" and isinstance(rec.get("message"), dict):
            entry = rec["message"]
        else:
            entry = rec.get("entry") if isinstance(rec.get("entry"), dict) else rec
        node_id = rec.get("id") or entry.get("id")
        if isinstance(node_id, str):
            nodes[node_id] = rec | entry | {"_record": rec}
            if rec.get("active") or rec.get("isCurrent") or typ in {"active", "session_info"}:
                active_id = node_id
        if typ in {"modelChange", "model_change"}:
            model = rec.get("model") or entry.get("model") or model
        if typ in {"thinkingLevelChange", "thinking_level_change"}:
            thinking_level = rec.get("thinkingLevel") or entry.get("thinkingLevel") or thinking_level
        if "compact" in typ.lower():
            compactions += 1
        role = rec.get("role") or entry.get("role") or typ
        if role == "assistant":
            key = str(rec.get("requestId") or rec.get("messageId") or node_id or i)
            if key not in seen_usage:
                seen_usage.add(key)
                u = _usage(entry)
                for k in total:
                    total[k] += u[k]  # type: ignore[operator]
            text = _content_text(entry.get("content") or rec.get("content"))
            turns.append({"role": "assistant", "text": text, "model": entry.get("model") or model or "unknown", **_usage(entry)})
            for block in (entry.get("content") if isinstance(entry.get("content"), list) else []):
                if isinstance(block, dict) and (block.get("type") in {"toolCall", "tool-call"} or block.get("toolCallId")):
                    tcid = str(block.get("id") or block.get("toolCallId") or "")
                    tool_calls.append({"id": tcid, "name": block.get("name") or block.get("toolName") or "unknown", "input": block.get("input") or {}})
        elif role in {"toolResult", "tool_result"} or typ in {"toolResult", "tool_result"}:
            tcid = str(rec.get("toolCallId") or entry.get("toolCallId") or entry.get("id") or "")
            tool_results[tcid] = entry
        elif role == "user":
            turns.append({"role": "user", "text": _content_text(entry.get("content") or rec.get("content"))})
    active_entries = []
    cur = active_id
    seen = set()
    while isinstance(cur, str) and cur in nodes and cur not in seen:
        seen.add(cur); active_entries.append(nodes[cur]); cur = nodes[cur].get("parentId")
    active_entries.reverse()
    return {
        "session_id": session_id, "cwd": cwd, "model": model or "unknown", "thinking_level": thinking_level,
        "turns": turns, "active_entries": active_entries or turns, "tool_calls": tool_calls, "tool_results": tool_results,
        "tool_call_count": len(tool_calls), "compaction_count": compactions,
        "total_input_tokens": total["input_tokens"], "total_output_tokens": total["output_tokens"],
        "cache_read_tokens": total["cache_read_tokens"], "cache_creation_tokens": total["cache_creation_tokens"],
        "total_cost_usd": total["cost_usd"], "estimated": False, "filepath": str(path),
    }


def parse_session_turns(filepath):
    return parse_session_jsonl(filepath).get("turns", [])


def parse_jsonl_for_quality(filepath):
    parsed = parse_session_jsonl(filepath)
    return {"session_id": parsed["session_id"], "turns": parsed["turns"], "tool_calls": parsed["tool_calls"], "compactions": parsed["compaction_count"], "estimated": False}
