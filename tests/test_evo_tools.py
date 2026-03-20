from pathlib import Path
from palimpsest.runtime.tools import resolve_tool_functions

EVO_ROOT = Path(__file__).parent.parent / "evo"


class TestFileOps:
    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        funcs = resolve_tool_functions(EVO_ROOT, ["read_file"])
        result = funcs["read_file"](path="hello.txt", workspace=str(tmp_path))
        assert result.success
        assert "world" in result.output

    def test_read_file_not_found(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, ["read_file"])
        result = funcs["read_file"](path="nope.txt", workspace=str(tmp_path))
        assert not result.success

    def test_write_file(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, ["write_file"])
        result = funcs["write_file"](path="new.txt", content="hello", workspace=str(tmp_path))
        assert result.success
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_write_file_creates_dirs(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, ["write_file"])
        result = funcs["write_file"](path="sub/dir/f.txt", content="nested", workspace=str(tmp_path))
        assert result.success
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        funcs = resolve_tool_functions(EVO_ROOT, ["list_files"])
        result = funcs["list_files"](path=".", workspace=str(tmp_path))
        assert result.success
        assert "a.txt" in result.output
        assert "b.txt" in result.output

    def test_list_files_not_a_dir(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, ["list_files"])
        result = funcs["list_files"](path="nonexistent", workspace=str(tmp_path))
        assert not result.success


class TestTaskComplete:
    def test_returns_terminal(self):
        funcs = resolve_tool_functions(EVO_ROOT, ["task_complete"])
        result = funcs["task_complete"](summary="all done", status="success")
        assert result.success
        assert result.terminal is True
        assert "all done" in result.output

    def test_tool_schema(self):
        funcs = resolve_tool_functions(EVO_ROOT, ["task_complete"])
        func = funcs["task_complete"]
        schema = func.__tool_schema__
        assert schema["function"]["name"] == "task_complete"
