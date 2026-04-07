"""Tests for bundle-only context resolution (Bundle MVP).

Per Bundle MVP: Context providers are loaded from evo/<bundle>/contexts/ only.
No global fallback, no team layer.
"""
import textwrap
from pathlib import Path

from palimpsest.runtime.contexts import resolve_context_functions


def test_resolve_context_discovers_bundle_providers(tmp_path):
    """Bundle context providers in evo/<bundle>/contexts/ are discovered."""
    ctx_dir = tmp_path / "factorio" / "contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "custom.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("custom")
        def custom_section(description: str = "Custom") -> str:
            return "## Custom\\nhello"
    """))

    registry = resolve_context_functions(tmp_path, ["custom"], bundle="factorio")
    assert "custom" in registry
    assert "hello" in registry["custom"]()


def test_resolve_context_ignores_unrequested(tmp_path):
    """Only requested providers are loaded."""
    ctx_dir = tmp_path / "factorio" / "contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "two.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("a")
        def section_a() -> str:
            return "a"

        @context_provider("b")
        def section_b() -> str:
            return "b"
    """))

    registry = resolve_context_functions(tmp_path, ["a"], bundle="factorio")
    assert "a" in registry
    assert "b" not in registry


def test_empty_bundle_returns_empty_registry(tmp_path):
    """When bundle is empty or not specified, returns empty registry."""
    # Create bundle context
    (tmp_path / "factorio" / "contexts").mkdir(parents=True)
    (tmp_path / "factorio" / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "bundle"
    """))
    
    # Empty bundle returns empty
    result = resolve_context_functions(tmp_path, ["foo"], bundle="")
    assert result == {}


def test_nonexistent_bundle_returns_empty_registry(tmp_path):
    """When bundle doesn't exist, returns empty registry."""
    # Create one bundle's context
    (tmp_path / "factorio" / "contexts").mkdir(parents=True)
    (tmp_path / "factorio" / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "factorio"
    """))
    
    # Different bundle returns empty
    result = resolve_context_functions(tmp_path, ["foo"], bundle="nonexistent")
    assert result == {}


def test_missing_provider_returns_empty_registry(tmp_path):
    """When requested provider doesn't exist in bundle, returns empty."""
    (tmp_path / "factorio" / "contexts").mkdir(parents=True)
    # No files in contexts
    
    result = resolve_context_functions(tmp_path, ["nonexistent"], bundle="factorio")
    assert result == {}


def test_ignores_underscore_prefixed_files(tmp_path):
    """Files starting with underscore are ignored."""
    ctx_dir = tmp_path / "factorio" / "contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "_private.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("private")
        def private(**_) -> str:
            return "private"
    """))
    (ctx_dir / "public.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("public")
        def public(**_) -> str:
            return "public"
    """))

    registry = resolve_context_functions(tmp_path, ["private", "public"], bundle="factorio")
    assert "private" not in registry  # In _private.py, ignored
    assert "public" in registry
    assert registry["public"]() == "public"