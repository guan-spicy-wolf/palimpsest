"""Tests for bundle workspace context resolution (ADR-0015).

Per ADR-0015: Context providers are loaded from bundle_workspace/contexts/ directly.
"""
import textwrap
from pathlib import Path

from palimpsest.runtime.contexts import resolve_context_functions


def test_resolve_context_discovers_bundle_providers(tmp_path):
    """Bundle context providers in bundle_workspace/contexts/ are discovered."""
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir(parents=True)
    (ctx_dir / "custom.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("custom")
        def custom_section(description: str = "Custom") -> str:
            return "## Custom\\nhello"
    """))

    registry = resolve_context_functions(tmp_path, ["custom"])
    assert "custom" in registry
    assert "hello" in registry["custom"]()


def test_resolve_context_ignores_unrequested(tmp_path):
    """Only requested providers are loaded."""
    ctx_dir = tmp_path / "contexts"
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

    registry = resolve_context_functions(tmp_path, ["a"])
    assert "a" in registry
    assert "b" not in registry


def test_empty_workspace_returns_empty_registry(tmp_path):
    """When bundle_workspace has no contexts directory, returns empty registry."""
    # No contexts directory
    result = resolve_context_functions(tmp_path, ["foo"])
    assert result == {}


def test_missing_provider_returns_empty_registry(tmp_path):
    """When requested provider doesn't exist in bundle, returns empty."""
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir(parents=True)
    # No .py files in contexts
    
    result = resolve_context_functions(tmp_path, ["nonexistent"])
    assert result == {}


def test_ignores_underscore_prefixed_files(tmp_path):
    """Files starting with underscore are ignored."""
    ctx_dir = tmp_path / "contexts"
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

    registry = resolve_context_functions(tmp_path, ["private", "public"])
    assert "private" not in registry  # In _private.py, ignored
    assert "public" in registry
    assert registry["public"]() == "public"