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
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    out = []
    for p in root.rglob("*.jsonl"):
        try:
            st = p.stat()
        except OSError:
            continue
        if st.st_mtime < cutoff:
            continue
        out.append((p, st.st_mtime, p.name))
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


def _usage_cost(u: dict[str, Any]) -> float:
    cost = u.get("cost")
    if isinstance(cost, dict):
        return _safe_float(cost.get("total"))
    return _safe_float(cost or u.get("totalCost") or u.get("costUsd"))


def _usage(d: dict[str, Any]) -> dict[str, int | float]:
    u = d.get("usage") if isinstance(d.get("usage"), dict) else d
    return {
        "input_tokens": _safe_int(u.get("input") or u.get("inputTokens") or u.get("promptTokens")),
        "output_tokens": _safe_int(u.get("output") or u.get("outputTokens") or u.get("completionTokens")),
        "cache_read_tokens": _safe_int(u.get("cacheRead") or u.get("cacheReadTokens")),
        "cache_creation_tokens": _safe_int(u.get("cacheWrite") or u.get("cacheWriteTokens")),
        "cost_usd": _usage_cost(u),
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
    model_usage: dict[str, int] = {}
    model_usage_breakdown: dict[str, dict[str, int]] = {}
    skills_used: dict[str, int] = {}
    subagents_used: dict[str, int] = {}
    tool_call_counts: dict[str, int] = {}
    first_ts = None
    last_ts = None
    topic = None
    version = None
    compactions = 0
    model = None
    thinking_level = None
    for i, rec in enumerate(entries):
        typ = str(rec.get("type") or rec.get("entryType") or rec.get("kind") or "")
        ts = _parse_ts(rec.get("timestamp") or rec.get("createdAt") or rec.get("time"))
        if ts is not None:
            first_ts = first_ts or ts
            last_ts = ts
        version = version or rec.get("version")
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
            turn_model = str(entry.get("model") or model or "unknown")
            if key not in seen_usage:
                seen_usage.add(key)
                u = _usage(entry)
                for k in total:
                    total[k] += u[k]  # type: ignore[operator]
                billable = int(u["input_tokens"] + u["cache_creation_tokens"] + u["output_tokens"])
                model_usage[turn_model] = model_usage.get(turn_model, 0) + billable
                bd = model_usage_breakdown.setdefault(turn_model, {"fresh_input": 0, "cache_read": 0, "cache_create": 0, "output": 0})
                bd["fresh_input"] += int(u["input_tokens"])
                bd["cache_read"] += int(u["cache_read_tokens"])
                bd["cache_create"] += int(u["cache_creation_tokens"])
                bd["output"] += int(u["output_tokens"])
            text = _content_text(entry.get("content") or rec.get("content"))
            usage = _usage(entry)
            turn = {
                "turn_index": len([t for t in turns if t.get("role") == "assistant"]),
                "role": "assistant",
                "input_tokens": int(usage["input_tokens"] + usage["cache_read_tokens"] + usage["cache_creation_tokens"]),
                "output_tokens": int(usage["output_tokens"]),
                "cache_read": int(usage["cache_read_tokens"]),
                "cache_creation": int(usage["cache_creation_tokens"]),
                "cache_creation_1h": 0,
                "cache_creation_5m": int(usage["cache_creation_tokens"]),
                "model": turn_model,
                "timestamp": rec.get("timestamp") or rec.get("createdAt") or rec.get("time"),
                "gap_since_prev_seconds": None,
                "tools_used": [],
                "cost_usd": float(usage["cost_usd"]),
                "estimated": False,
                "text": text,
            }
            turns.append(turn)
            for block in (entry.get("content") if isinstance(entry.get("content"), list) else []):
                if isinstance(block, dict) and (block.get("type") in {"toolCall", "tool-call", "tool_use"} or block.get("toolCallId")):
                    tcid = str(block.get("id") or block.get("toolCallId") or "")
                    name = str(block.get("name") or block.get("toolName") or "unknown")
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    tool_calls.append({"id": tcid, "name": name, "input": inp})
                    turn["tools_used"].append(name)
                    tool_call_counts[name] = tool_call_counts.get(name, 0) + 1
                    if name == "Skill":
                        skill = str(inp.get("skill") or "unknown")
                        skills_used[skill] = skills_used.get(skill, 0) + 1
                    elif name in {"Task", "Agent"}:
                        agent = str(inp.get("subagent_type") or "unknown")
                        subagents_used[agent] = subagents_used.get(agent, 0) + 1
        elif role in {"toolResult", "tool_result"} or typ in {"toolResult", "tool_result"}:
            tcid = str(rec.get("toolCallId") or entry.get("toolCallId") or entry.get("id") or "")
            tool_results[tcid] = entry
        elif role == "user":
            text = _content_text(entry.get("content") or rec.get("content"))
            if topic is None and text.strip():
                topic = text.strip().splitlines()[0][:120]
            turns.append({"role": "user", "text": text})
    active_entries = []
    cur = active_id
    seen = set()
    while isinstance(cur, str) and cur in nodes and cur not in seen:
        seen.add(cur); active_entries.append(nodes[cur]); cur = nodes[cur].get("parentId")
    active_entries.reverse()
    duration_minutes = 0.0
    if first_ts and last_ts:
        duration_minutes = max(0.0, (last_ts - first_ts).total_seconds() / 60.0)
    full_input = int(total["input_tokens"] + total["cache_read_tokens"] + total["cache_creation_tokens"])
    cache_hit_rate = (float(total["cache_read_tokens"]) / full_input) if full_input else 0.0
    return {
        "session_id": session_id, "cwd": cwd, "model": model or "unknown", "thinking_level": thinking_level,
        "turns": turns, "active_entries": active_entries or turns, "tool_calls": tool_call_counts,
        "pi_tool_calls": tool_calls, "tool_results": tool_results,
        "tool_call_count": len(tool_calls), "compaction_count": compactions, "compactions": compactions,
        "duration_minutes": duration_minutes, "message_count": len(turns), "api_calls": len(seen_usage),
        "total_input_tokens": full_input, "total_output_tokens": int(total["output_tokens"]),
        "total_cache_read": int(total["cache_read_tokens"]), "total_cache_create": int(total["cache_creation_tokens"]),
        "total_cache_create_1h": 0, "total_cache_create_5m": int(total["cache_creation_tokens"]),
        "cache_read_tokens": int(total["cache_read_tokens"]), "cache_creation_tokens": int(total["cache_creation_tokens"]),
        "cache_hit_rate": cache_hit_rate, "avg_call_gap_seconds": None, "max_call_gap_seconds": None, "p95_call_gap_seconds": None,
        "model_usage": model_usage, "model_usage_breakdown": model_usage_breakdown,
        "skills_used": skills_used, "subagents_used": subagents_used, "version": version,
        "slug": None, "topic": topic, "first_ts": first_ts.isoformat() if first_ts else None, "is_sidechain": False,
        "total_cost_usd": total["cost_usd"], "estimated": False, "filepath": str(path),
    }


def parse_session_turns(filepath):
    return [turn for turn in parse_session_jsonl(filepath).get("turns", []) if turn.get("role") == "assistant"]


def _tool_result_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return _content_text(value.get("content") or value.get("text") or value.get("result") or value.get("output"))
    if isinstance(value, list):
        return _content_text(value)
    return str(value or "")


def _entry_is_failure(rec: dict[str, Any], entry: dict[str, Any]) -> bool:
    """Best-effort Pi tool-result failure flag propagation."""
    for source in (entry, rec):
        if bool(source.get("isError") or source.get("is_error") or source.get("error")):
            return True
        details = source.get("details")
        if isinstance(details, dict) and bool(details.get("isError") or details.get("is_error") or details.get("error")):
            return True
        status = str(source.get("status") or source.get("outcome") or "").lower()
        if status in {"error", "failed", "failure"}:
            return True
    return False


def _context_entry_text(rec: dict[str, Any], entry: dict[str, Any], typ: str, role: str) -> str:
    """Extract context-bearing text from Pi custom/context summary entries."""
    if role not in {"custom_message", "customMessage", "branch_summary", "branchSummary"} and typ not in {"custom_message", "customMessage", "branch_summary", "branchSummary"}:
        return ""
    return _content_text(
        entry.get("content")
        or entry.get("summary")
        or entry.get("text")
        or entry.get("message")
        or rec.get("content")
        or rec.get("summary")
        or rec.get("text")
        or rec.get("message")
    )


def parse_jsonl_for_quality(filepath):
    path = Path(filepath)
    parsed = parse_session_jsonl(path)
    reads = []
    writes = []
    tool_results = []
    tool_result_meta = []
    system_reminders = []
    messages = []
    agent_dispatches = []
    decisions = []
    tool_name_by_id = {}
    tool_calls = 0
    compactions = 0
    context_tokens = None
    current_model = parsed.get("model")

    for idx, rec in enumerate(_iter_records(path) or []):
        typ = str(rec.get("type") or rec.get("entryType") or rec.get("kind") or "")
        if typ == "message" and isinstance(rec.get("message"), dict):
            entry = rec["message"]
        else:
            entry = rec.get("entry") if isinstance(rec.get("entry"), dict) else rec
        role = rec.get("role") or entry.get("role") or typ
        ts = str(rec.get("timestamp") or rec.get("createdAt") or rec.get("time") or "")

        if "compact" in typ.lower():
            compactions += 1
            reads = []
            writes = []
            tool_results = []
            tool_result_meta = []
            system_reminders = []
            messages = []
            agent_dispatches = []
            decisions = []
            tool_name_by_id = {}
            context_tokens = None
            continue

        if role == "system" or typ == "system":
            msg_content = _content_text(entry.get("content") or rec.get("content") or rec.get("message"))
            if "system-reminder" in msg_content:
                import hashlib
                system_reminders.append((idx, hashlib.sha256(msg_content.encode()).hexdigest()[:16], len(msg_content)))

        context_text = _context_entry_text(rec, entry, typ, str(role))
        if context_text:
            messages.append((idx, "system", len(context_text), len(context_text.split()) > 10))

        if role == "user":
            text = _content_text(entry.get("content") or rec.get("content"))
            messages.append((idx, "user", len(text), len(text.split()) > 10))

        if role == "assistant":
            usage = _usage(entry)
            tok = int(usage["input_tokens"] + usage["cache_read_tokens"] + usage["cache_creation_tokens"])
            if tok > 0:
                context_tokens = tok
            current_model = entry.get("model") or current_model
            text_length = 0
            is_substantive = False
            for block in (entry.get("content") if isinstance(entry.get("content"), list) else []):
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype in {"text", "message"}:
                    txt = str(block.get("text") or "")
                    text_length += len(txt)
                    is_substantive = is_substantive or len(txt.split()) > 20
                    if any(word in txt.lower() for word in ("decided", "decision", "therefore", "i will")):
                        decisions.append((idx, txt[:200].strip()))
                if btype in {"toolCall", "tool-call", "tool_use"} or block.get("toolCallId"):
                    is_substantive = True
                    tool_calls += 1
                    name = str(block.get("name") or block.get("toolName") or "unknown")
                    tid = str(block.get("id") or block.get("toolCallId") or "")
                    if tid:
                        tool_name_by_id[tid] = name
                    inp = block.get("input") if isinstance(block.get("input"), dict) else {}
                    file_path = str(inp.get("file_path") or inp.get("path") or "")
                    if name in {"Read", "read"} and file_path:
                        reads.append((idx, file_path, ts))
                    elif name in {"Edit", "Write", "edit", "write"} and file_path:
                        writes.append((idx, file_path, ts))
                    elif name in {"Task", "Agent"}:
                        agent_dispatches.append((idx, len(str(inp.get("prompt") or "")), 0))
            messages.append((idx, "assistant", text_length, is_substantive))

        if role in {"toolResult", "tool_result"} or typ in {"toolResult", "tool_result"}:
            tid = str(rec.get("toolCallId") or entry.get("toolCallId") or entry.get("id") or "")
            text = _tool_result_text(entry)
            is_failure = _entry_is_failure(rec, entry)
            tool_results.append((idx, tid, len(text), is_failure))
            tool_result_meta.append({"index": idx, "tool_id": tid, "tool_name": tool_name_by_id.get(tid, ""), "size": len(text), "is_failure": is_failure})
            if agent_dispatches and agent_dispatches[-1][2] == 0:
                last = agent_dispatches[-1]
                agent_dispatches[-1] = (last[0], last[1], len(text))

    if not messages:
        return None
    return {
        "session_id": parsed["session_id"],
        "reads": reads,
        "writes": writes,
        "tool_results": tool_results,
        "tool_result_meta": tool_result_meta,
        "system_reminders": system_reminders,
        "messages": messages,
        "compactions": compactions,
        "tool_calls": tool_calls,
        "agent_dispatches": agent_dispatches,
        "decisions": decisions,
        "total_entries": len(list(_iter_records(path) or [])),
        "context_tokens": context_tokens,
        "model": current_model,
        "turns": parsed["turns"],
        "estimated": False,
    }
