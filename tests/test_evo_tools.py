from pathlib import Path
from palimpsest.gateway.tool_loader import resolve_tool_providers

EVO_ROOT = Path(__file__).parent.parent / "evo"


class TestFileOps:
    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "hello.txt"}, str(tmp_path))
        assert result.success
        assert "world" in result.output

    def test_read_file_not_found(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "nope.txt"}, str(tmp_path))
        assert not result.success

    def test_read_file_path_traversal(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["read_file"])
        result = providers["read_file"].execute("read_file", {"path": "../../etc/passwd"}, str(tmp_path))
        assert not result.success

    def test_write_file(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["write_file"])
        result = providers["write_file"].execute("write_file", {"path": "new.txt", "content": "hello"}, str(tmp_path))
        assert result.success
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_write_file_creates_dirs(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["write_file"])
        result = providers["write_file"].execute("write_file", {"path": "sub/dir/f.txt", "content": "nested"}, str(tmp_path))
        assert result.success
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        providers = resolve_tool_providers(EVO_ROOT, ["list_files"])
        result = providers["list_files"].execute("list_files", {"path": "."}, str(tmp_path))
        assert result.success
        assert "a.txt" in result.output
        assert "b.txt" in result.output

    def test_list_files_not_a_dir(self, tmp_path):
        providers = resolve_tool_providers(EVO_ROOT, ["list_files"])
        result = providers["list_files"].execute("list_files", {"path": "nonexistent"}, str(tmp_path))
        assert not result.success


class TestTaskComplete:
    def test_returns_terminal(self):
        providers = resolve_tool_providers(EVO_ROOT, ["task_complete"])
        result = providers["task_complete"].execute(
            "task_complete", {"summary": "all done", "status": "success"}, "/tmp"
        )
        assert result.success
        assert result.terminal is True
        assert "all done" in result.output

    def test_tool_spec(self):
        providers = resolve_tool_providers(EVO_ROOT, ["task_complete"])
        specs = providers["task_complete"].tools()
        assert any(s.name == "task_complete" for s in specs)
