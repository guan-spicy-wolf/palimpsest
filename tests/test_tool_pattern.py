"""Tests for tool pattern detection (Factorio Tool Evolution MVP)."""
import pytest

from palimpsest.runtime.tool_pattern import (
    ToolCallRecord,
    RepetitionFinding,
    detect_repetition,
)


def test_detect_no_repetition():
    """No repetition detected when call count is below threshold."""
    history = [
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 0, "y": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 1, "y": 0}'),
    ]
    findings = detect_repetition(history, min_count=5)
    assert len(findings) == 0


def test_detect_repetition_dispatcher_groups_by_script_name():
    """Dispatcher tools are grouped by script_name (args.name field)."""
    history = [
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 0, "y": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 1, "y": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 2, "y": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 3, "y": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 4, "y": 0}'),
    ]
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    assert len(findings) == 1
    assert findings[0].tool_name == "factorio_call_script(actions.place)"
    assert findings[0].call_count == 5
    assert findings[0].arg_pattern == "actions.place"
    assert findings[0].similarity >= 0.7


def test_detect_repetition_groups_by_tool_name():
    """Non-dispatcher tools are grouped by tool name."""
    history = [
        ToolCallRecord("read_file", '{"path": "/tmp/a.txt"}'),
        ToolCallRecord("read_file", '{"path": "/tmp/b.txt"}'),
        ToolCallRecord("read_file", '{"path": "/tmp/c.txt"}'),
        ToolCallRecord("read_file", '{"path": "/tmp/d.txt"}'),
        ToolCallRecord("read_file", '{"path": "/tmp/e.txt"}'),
    ]
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    assert len(findings) == 1
    assert findings[0].tool_name == "read_file"
    assert findings[0].call_count == 5
    assert findings[0].arg_pattern == "read_file"


def test_detect_multiple_patterns():
    """Multiple patterns detected when different scripts are called repeatedly."""
    history = [
        # 5 calls to actions.place
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 1}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 2}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 3}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 4}'),
        # 5 calls to actions.teleport
        ToolCallRecord("factorio_call_script", '{"name": "actions.teleport", "x": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.teleport", "x": 1}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.teleport", "x": 2}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.teleport", "x": 3}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.teleport", "x": 4}'),
    ]
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    assert len(findings) == 2
    patterns = {f.arg_pattern: f for f in findings}
    assert "actions.place" in patterns
    assert "actions.teleport" in patterns


def test_detect_repetition_below_similarity_threshold():
    """No detection when arg key similarity is below threshold."""
    history = [
        ToolCallRecord("tool", '{"a": 1}'),
        ToolCallRecord("tool", '{"b": 2}'),
        ToolCallRecord("tool", '{"c": 3}'),
        ToolCallRecord("tool", '{"d": 4}'),
        ToolCallRecord("tool", '{"e": 5}'),
    ]
    # All keys are different, similarity = 0
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.5)
    assert len(findings) == 0


def test_detect_repetition_high_similarity():
    """Detection when arg keys are mostly the same."""
    history = [
        ToolCallRecord("tool", '{"x": 0, "y": 0, "z": 0}'),
        ToolCallRecord("tool", '{"x": 1, "y": 1, "z": 1}'),
        ToolCallRecord("tool", '{"x": 2, "y": 2, "z": 2}'),
        ToolCallRecord("tool", '{"x": 3, "y": 3, "z": 3}'),
        ToolCallRecord("tool", '{"x": 4, "y": 4, "z": 4}'),
    ]
    # All keys are the same, similarity = 1.0
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    assert len(findings) == 1
    assert findings[0].similarity == 1.0


def test_detect_empty_history():
    """Empty history returns no findings."""
    findings = detect_repetition([], min_count=5)
    assert len(findings) == 0


def test_tool_call_record_dataclass():
    """ToolCallRecord dataclass stores name and args."""
    rec = ToolCallRecord(name="tool", args_json='{"a": 1}')
    assert rec.name == "tool"
    assert rec.args_json == '{"a": 1}'


def test_repetition_finding_dataclass():
    """RepetitionFinding dataclass stores detection result."""
    finding = RepetitionFinding(
        tool_name="tool",
        call_count=10,
        arg_pattern="pattern",
        similarity=0.85,
    )
    assert finding.tool_name == "tool"
    assert finding.call_count == 10
    assert finding.arg_pattern == "pattern"
    assert finding.similarity == 0.85


def test_detect_mixed_dispatcher_and_regular_tools():
    """Mixed dispatcher and regular tools are grouped separately."""
    history = [
        # 3 dispatcher calls (below threshold)
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 0}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 1}'),
        ToolCallRecord("factorio_call_script", '{"name": "actions.place", "x": 2}'),
        # 5 regular tool calls (meets threshold)
        ToolCallRecord("read_file", '{"path": "a"}'),
        ToolCallRecord("read_file", '{"path": "b"}'),
        ToolCallRecord("read_file", '{"path": "c"}'),
        ToolCallRecord("read_file", '{"path": "d"}'),
        ToolCallRecord("read_file", '{"path": "e"}'),
    ]
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    # Only read_file meets threshold
    assert len(findings) == 1
    assert findings[0].tool_name == "read_file"


def test_detect_invalid_json_args():
    """Invalid JSON args are handled gracefully (empty dict, reduces similarity)."""
    history = [
        ToolCallRecord("tool", '{"a": 1}'),
        ToolCallRecord("tool", 'invalid json'),
        ToolCallRecord("tool", '{"a": 2}'),
        ToolCallRecord("tool", '{"a": 3}'),
        ToolCallRecord("tool", '{"a": 4}'),
    ]
    findings = detect_repetition(history, min_count=5, similarity_threshold=0.7)
    # Invalid JSON reduces similarity (4 valid + 1 empty = avg ~0.6)
    # Below 0.7 threshold, no detection
    assert len(findings) == 0
    
    # With lower threshold, should detect
    findings_low = detect_repetition(history, min_count=5, similarity_threshold=0.5)
    assert len(findings_low) == 1