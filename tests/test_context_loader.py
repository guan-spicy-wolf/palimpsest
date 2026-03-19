import textwrap
from pathlib import Path


def test_resolve_context_discovers_providers(tmp_path):
    from palimpsest.stages.context import resolve_context_providers

    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    (ctx_dir / "__init__.py").write_text("")
    (ctx_dir / "custom.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ContextProvider

        class CustomSection(ContextProvider):
            @property
            def section_type(self) -> str:
                return "custom"
            def render(self, job_id, workspace, section_config, runtime_deps=None):
                return "## Custom\\nhello"
    """))

    providers = resolve_context_providers(tmp_path, ["custom"])
    assert "custom" in providers
    assert "hello" in providers["custom"].render("j1", "/tmp", {})


def test_resolve_context_ignores_unrequested(tmp_path):
    from palimpsest.stages.context import resolve_context_providers

    ctx_dir = tmp_path / "contexts"
    ctx_dir.mkdir()
    (ctx_dir / "__init__.py").write_text("")
    (ctx_dir / "two.py").write_text(textwrap.dedent("""\
        from palimpsest.runtime.interfaces import ContextProvider

        class AProvider(ContextProvider):
            @property
            def section_type(self): return "a"
            def render(self, job_id, workspace, section_config, runtime_deps=None): return "a"

        class BProvider(ContextProvider):
            @property
            def section_type(self): return "b"
            def render(self, job_id, workspace, section_config, runtime_deps=None): return "b"
    """))

    providers = resolve_context_providers(tmp_path, ["a"])
    assert "a" in providers
    assert "b" not in providers
