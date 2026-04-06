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


def test_team_context_overrides_global(tmp_path):
    """Team-specific context provider has higher priority than global."""
    # Create global context
    (tmp_path / "contexts").mkdir()
    (tmp_path / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "global"
    """))
    
    # Create team-specific context
    (tmp_path / "teams" / "factorio" / "contexts").mkdir(parents=True)
    (tmp_path / "teams" / "factorio" / "contexts" / "test.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("foo")
        def foo(**_) -> str:
            return "team"
    """))
    
    # Team context should be prioritized
    result = resolve_context_functions(tmp_path, ["foo"], team="factorio")
    assert "foo" in result
    assert result["foo"]() == "team"  # team version wins
    
    # Default team uses global
    result_default = resolve_context_functions(tmp_path, ["foo"], team="default")
    assert result_default["foo"]() == "global"


def test_team_context_fallback_to_global(tmp_path):
    """When team context doesn't have requested provider, fall back to global."""
    # Create global context only
    (tmp_path / "contexts").mkdir()
    (tmp_path / "contexts" / "global.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.contexts import context_provider

        @context_provider("shared")
        def shared(**_) -> str:
            return "global_shared"
    """))
    
    # Team without its own context
    (tmp_path / "teams" / "factorio").mkdir(parents=True)
    # No contexts subdirectory for factorio team
    
    result = resolve_context_functions(tmp_path, ["shared"], team="factorio")
    assert "shared" in result
    assert result["shared"]() == "global_shared"  # fallback to global
