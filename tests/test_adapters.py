import json
import tempfile
from pathlib import Path

from backend.adapters.registry import get_adapter


def test_opencode_writes():
    a = get_adapter("opencode")
    with tempfile.TemporaryDirectory() as d:
        wt = Path(d)

        # auto_approve mode → all-allow config
        p = a.write_permission(wt, {"mode": "auto_approve"})
        cfg = json.loads(p.read_text())
        assert cfg["permission"]["edit"] == "allow"
        assert cfg["permission"]["bash"] == "allow"
        assert cfg["permission"]["webfetch"] == "allow"

        # direct rules mode → rules written as-is
        p = a.write_permission(wt, {"edit": "deny", "bash": "ask"})
        cfg = json.loads(p.read_text())
        assert cfg["permission"]["edit"] == "deny"
        assert cfg["permission"]["bash"] == "ask"

        i = a.write_instructions(wt, "# rules\nbe strict")
        assert "be strict" in i.read_text()
        s = a.write_skills(wt, {"tdd": "# tdd\nwrite tests first"})
        assert s[0].exists() and "tdd" in s[0].read_text()
        assert a.aionui_preset_agent_type() == "acp"


def test_claude_code_writes():
    a = get_adapter("claude_code")
    with tempfile.TemporaryDirectory() as d:
        wt = Path(d)
        p = a.write_permission(wt, {"edit": "deny", "bash": "deny"})
        assert ".claude/settings.json" in str(p)
        assert p.exists()
        i = a.write_instructions(wt, "# claude rules")
        assert "CLAUDE.md" in str(i)
        assert i.exists()
        s = a.write_skills(wt, {"review": "# review checklist"})
        assert s[0].exists()
        assert a.aionui_preset_agent_type() == "acp"


def test_gemini_writes():
    a = get_adapter("gemini")
    with tempfile.TemporaryDirectory() as d:
        wt = Path(d)
        p = a.write_permission(wt, {"edit": "deny"})
        assert ".gemini/permissions.json" in str(p)
        i = a.write_instructions(wt, "# gemini rules")
        assert "GEMINI.md" in str(i)
        s = a.write_skills(wt, {"test": "# test skill"})
        assert s[0].exists()
        assert a.aionui_preset_agent_type() == "acp"


def test_unknown_engine():
    import pytest
    with pytest.raises(ValueError):
        get_adapter("nonexistent")
