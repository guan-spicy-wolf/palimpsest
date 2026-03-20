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
