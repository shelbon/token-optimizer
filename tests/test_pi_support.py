import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugins/token-optimizer/skills/token-optimizer/scripts"


def run_bridge(cmd, payload, env):
    p = subprocess.run([sys.executable, str(SCRIPTS / "pi_bridge.py"), cmd], input=json.dumps(payload), text=True, capture_output=True, env=env, timeout=5)
    return p


def env_for(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    pi = home / ".pi" / "agent"; pi.mkdir(parents=True)
    env = os.environ.copy(); env.update({"HOME": str(home), "TOKEN_OPTIMIZER_RUNTIME": "pi", "PI_CODING_AGENT_DIR": str(pi), "TOKEN_OPTIMIZER_SNAPSHOT_DIR": str(pi / "token-optimizer" / "data"), "PYTHONPATH": str(SCRIPTS), "TOKEN_OPTIMIZER_NO_PROC_SCAN": "1"})
    return env, pi


def test_pi_runtime_detection_and_home(tmp_path, monkeypatch):
    env, pi = env_for(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("TOKEN_OPTIMIZER_RUNTIME", "pi")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import runtime_env
    importlib.reload(runtime_env)
    runtime_env.detect_runtime.cache_clear()
    assert runtime_env.detect_runtime() == "pi"
    assert runtime_env.pi_home() == pi.resolve()
    assert runtime_env.runtime_home() == pi.resolve()
    assert runtime_env.runtime_name_for_humans() == "Pi"
    assert runtime_env.plugin_data_env_vars() == ("TOKEN_OPTIMIZER_PLUGIN_DATA",)


def test_pi_invalid_home_rejected_no_claude_fallback(tmp_path, monkeypatch):
    home = tmp_path / "home"; home.mkdir()
    bad = tmp_path / "outside"; bad.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("TOKEN_OPTIMIZER_RUNTIME", "pi")
    monkeypatch.setenv("PI_CODING_AGENT_DIR", str(bad))
    monkeypatch.syspath_prepend(str(SCRIPTS))
    import runtime_env
    importlib.reload(runtime_env)
    assert runtime_env.runtime_home() == home / ".pi" / "agent"
    assert runtime_env.runtime_home() != home / ".claude"


def test_bridge_empty_and_malformed_json_fail_open(tmp_path):
    env, _pi = env_for(tmp_path)
    empty = subprocess.run([sys.executable, str(SCRIPTS / "pi_bridge.py"), "read-before"], input="", text=True, capture_output=True, env=env)
    assert json.loads(empty.stdout)["action"] == "allow"
    bad = subprocess.run([sys.executable, str(SCRIPTS / "pi_bridge.py"), "read-before"], input="{", text=True, capture_output=True, env=env)
    assert json.loads(bad.stdout)["action"] == "allow"
    assert bad.stderr


def test_bridge_read_repeated_replaced_and_invalidated(tmp_path):
    env, pi = env_for(tmp_path)
    project = tmp_path / "proj"; project.mkdir()
    f = project / "big.py"; f.write_text("print('x')\n" * 600)
    first = run_bridge("read-before", {"path": str(f), "cwd": str(project), "session_id": "s1"}, env)
    assert json.loads(first.stdout)["action"] == "allow"
    second = run_bridge("read-before", {"path": str(f), "cwd": str(project), "session_id": "s1"}, env)
    out = json.loads(second.stdout)
    assert out["action"] == "replace"
    assert "expand" in out["content"]
    inv = run_bridge("read-invalidate", {"path": str(f), "cwd": str(project), "session_id": "s1"}, env)
    assert json.loads(inv.stdout)["action"] == "invalidated"
    third = run_bridge("read-before", {"path": str(f), "cwd": str(project), "session_id": "s1"}, env)
    assert json.loads(third.stdout)["action"] == "allow"
    assert not (Path(env["HOME"]) / ".claude").exists()


def test_bridge_bash_compress_archives(tmp_path):
    env, pi = env_for(tmp_path)
    text = "col1 col2\n" + "\n".join(f"row{i} value{i}" for i in range(1000))
    p = run_bridge("bash-result", {"command": "printf rows", "text": text, "returncode": 0, "session_id": "s2"}, env)
    out = json.loads(p.stdout)
    assert out["action"] in {"allow", "replace"}
    if out["action"] == "replace":
        assert "expand" in out["content"]
        assert list((pi / "token-optimizer" / "data" / "tool-archive").rglob("*.txt"))
    fail = run_bridge("bash-result", {"command": "bad", "text": text, "returncode": 1, "session_id": "s2"}, env)
    assert json.loads(fail.stdout)["action"] == "allow"


def test_pi_session_parser_branch_usage_and_malformed(tmp_path, monkeypatch):
    env, pi = env_for(tmp_path)
    monkeypatch.setenv("HOME", env["HOME"]); monkeypatch.setenv("TOKEN_OPTIMIZER_RUNTIME", "pi"); monkeypatch.setenv("PI_CODING_AGENT_DIR", str(pi)); monkeypatch.syspath_prepend(str(SCRIPTS))
    sess = pi / "sessions"; sess.mkdir(parents=True)
    f = sess / "abc.jsonl"
    rows = [
        {"sessionId": "abc", "cwd": "/tmp/p"},
        {"id": "u1", "role": "user", "content": "hi"},
        {"id": "a1", "parentId": "u1", "active": True, "type": "message", "message": {"role": "assistant", "model": "m", "usage": {"input": 10, "output": 5, "cacheRead": 3, "cacheWrite": 2, "cost": 0.01}, "content": [{"type": "toolCall", "id": "tc1", "name": "read", "input": {"path": "x"}}]}},
        {"type": "toolResult", "toolCallId": "tc1", "content": "ok"},
        {"type": "modelChange", "model": "m2"},
        {"type": "compaction"},
        {"id": "abandoned", "parentId": "u1", "role": "assistant", "usage": {"input": 20, "output": 1}},
        "{bad",
    ]
    f.write_text("\n".join(json.dumps(r) if isinstance(r, dict) else r for r in rows))
    import pi_session
    importlib.reload(pi_session)
    parsed = pi_session.parse_session_jsonl(f)
    assert parsed["session_id"] == "abc"
    assert isinstance(pi_session.find_all_jsonl_files()[0][0], Path)
    assert parsed["total_input_tokens"] == 35
    assert parsed["cache_read_tokens"] == 3
    assert parsed["duration_minutes"] == 0.0
    assert parsed["message_count"] == 3
    assert parsed["cache_hit_rate"] == 3 / 35
    assert parsed["model_usage"] == {"m": 17, "m2": 21}
    assert parsed["model_usage_breakdown"]["m"] == {"fresh_input": 10, "cache_read": 3, "cache_create": 2, "output": 5}
    assert parsed["skills_used"] == {}
    assert parsed["subagents_used"] == {}
    assert parsed["version"] is None
    assert parsed["tool_call_count"] == 1
    assert parsed["compaction_count"] == 1
    assert len(parsed["active_entries"]) == 2
    quality = pi_session.parse_jsonl_for_quality(f)
    assert quality["messages"]
    assert quality["reads"] == []
    assert quality["tool_results"] == []
    assert quality["system_reminders"] == []
    assert quality["agent_dispatches"] == []
    assert quality["decisions"] == []
    assert quality["tool_calls"] == 1
