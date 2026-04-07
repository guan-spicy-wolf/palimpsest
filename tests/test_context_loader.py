import textwrap
from pathlib import Path

from palimpsest.runtime.contexts import resolve_context_functions


def test_resolve_context_discovers_providers(tmp_path):
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
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
    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
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


def test_bundle_context_resolution(tmp_path):
    """Team-specific context provider has higher priority than global."""
    # Create global context
    (tmp_path / "contexts").mkdir()
    (tmp_path / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "global"
    """))
    
    # Create bundle-specific context
    (tmp_path / "factorio" / "contexts").mkdir(parents=True)
    (tmp_path / "factorio" / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "bundle"
    """))
    
    # Team context should be prioritized
    result = resolve_context_functions(tmp_path, ["foo"], bundle="factorio")
    assert "foo" in result
    assert result["foo"]() == "bundle"  # bundle version wins
    
    # Default bundle uses global
    result_default = resolve_context_functions(tmp_path, ["foo"], bundle="")
    assert result_default["foo"]() == "global"


def test_bundle_context_fallback(tmp_path):
    """When bundle context doesn't have requested provider, fall back to global."""
    # Create global context only
    (tmp_path / "contexts").mkdir()
    (tmp_path / "contexts" / "global.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("shared")
        def shared(**_) -> str:
            return "global_shared"
    """))
    
    # Team without its own context
    (tmp_path / "factorio").mkdir(parents=True)
    # No contexts subdirectory for factorio bundle
    
    result = resolve_context_functions(tmp_path, ["shared"], bundle="factorio")
    assert "shared" in result
    assert result["shared"]() == "global_shared"  # fallback to global
