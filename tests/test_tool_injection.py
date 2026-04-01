"""Tests for tool parameter injection."""

import tempfile
from pathlib import Path

from palimpsest.config import ToolsConfig
from palimpsest.runtime.context import RuntimeContext
from palimpsest.runtime.event_gateway import EventGateway
from palimpsest.runtime.tools import UnifiedToolGateway, tool


class MockEmitter:
    """Mock emitter for testing."""
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)
        return None

    def close(self):
        return None


def test_tool_receives_runtime_context_via_injection():
    """Tool declaring runtime_context parameter receives it via injection."""
    received = []

    @tool
    def probe(runtime_context: RuntimeContext) -> str:
        received.append(runtime_context.team)
        return "ok"

    # Verify runtime_context NOT in schema
    schema = probe.__tool_schema__
    assert "runtime_context" not in schema["function"]["parameters"]["properties"]

    # Create gateway with the probe tool
    with tempfile.TemporaryDirectory() as tmpdir:
        evo_root = Path(tmpdir)
        (evo_root / "tools").mkdir()
        
        event_gateway = EventGateway(MockEmitter())
        config = ToolsConfig(builtin={}, disabled_builtins=[])
        
        gateway = UnifiedToolGateway(
            config=config,
            evo_root=evo_root,
            requested_evo_tools=[],
            gateway=event_gateway,
        )
        
        # Manually inject the probe tool for testing
        gateway._functions["probe"] = probe
        gateway._schemas.append(probe.__tool_schema__)

        # Execute with injection
        ctx = RuntimeContext(team="factorio")
        result = gateway.execute("probe", "call-1", {}, "/tmp/ws", runtime_context=ctx)
        assert result.success
        assert received == ["factorio"]


def test_runtime_context_not_in_schema_for_injected_tool():
    """runtime_context should not appear in tool schema shown to LLM."""
    
    @tool
    def my_tool(runtime_context: RuntimeContext, name: str) -> str:
        return f"Hello {name}"

    schema = my_tool.__tool_schema__
    
    # runtime_context should NOT be in schema
    properties = schema["function"]["parameters"]["properties"]
    assert "runtime_context" not in properties
    
    # But regular params should be
    assert "name" in properties
    assert schema["function"]["parameters"]["required"] == ["name"]


def test_execute_accepts_runtime_context_parameter():
    """UnifiedToolGateway.execute() accepts runtime_context parameter."""
    received_context = []

    @tool
    def inspect_context(runtime_context: RuntimeContext) -> str:
        received_context.append(runtime_context)
        return f"job_id={runtime_context.job_id}"

    with tempfile.TemporaryDirectory() as tmpdir:
        evo_root = Path(tmpdir)
        (evo_root / "tools").mkdir()
        
        event_gateway = EventGateway(MockEmitter())
        config = ToolsConfig(builtin={}, disabled_builtins=[])
        
        gateway = UnifiedToolGateway(
            config=config,
            evo_root=evo_root,
            requested_evo_tools=[],
            gateway=event_gateway,
        )
        
        gateway._functions["inspect_context"] = inspect_context
        gateway._schemas.append(inspect_context.__tool_schema__)

        ctx = RuntimeContext(job_id="test-job-123", team="test-team")
        result = gateway.execute("inspect_context", "call-2", {}, "/tmp/ws", runtime_context=ctx)
        
        assert result.success
        assert result.output == "job_id=test-job-123"
        assert received_context[0].job_id == "test-job-123"


def test_tool_without_runtime_context_still_works():
    """Tools not declaring runtime_context work as before."""
    
    @tool
    def simple_tool(name: str) -> str:
        return f"Hello {name}"

    with tempfile.TemporaryDirectory() as tmpdir:
        evo_root = Path(tmpdir)
        (evo_root / "tools").mkdir()
        
        event_gateway = EventGateway(MockEmitter())
        config = ToolsConfig(builtin={}, disabled_builtins=[])
        
        gateway = UnifiedToolGateway(
            config=config,
            evo_root=evo_root,
            requested_evo_tools=[],
            gateway=event_gateway,
        )
        
        gateway._functions["simple_tool"] = simple_tool
        gateway._schemas.append(simple_tool.__tool_schema__)

        # Should work with or without runtime_context
        result = gateway.execute("simple_tool", "call-3", {"name": "World"}, "/tmp/ws")
        assert result.success
        assert result.output == "Hello World"
        
        # Should also work when runtime_context is passed but tool doesn't use it
        ctx = RuntimeContext(team="unused")
        result = gateway.execute("simple_tool", "call-4", {"name": "Again"}, "/tmp/ws", runtime_context=ctx)
        assert result.success
        assert result.output == "Hello Again"