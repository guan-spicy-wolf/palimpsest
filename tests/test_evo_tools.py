from pathlib import Path

import git

from palimpsest.runtime.tools import resolve_tool_functions, spawn

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

def test_task_complete_tool_is_removed():
    funcs = resolve_tool_functions(EVO_ROOT, ["task_complete"])
    assert funcs == {}


class TestSpawn:
    def test_spawn_normalizes_goal_role_and_defaults(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        with repo.config_writer() as writer:
            writer.set_value("user", "name", "Test Agent")
            writer.set_value("user", "email", "agent@example.com")
        (tmp_path / "README.md").write_text("hello\n")
        repo.index.add(["README.md"])
        repo.index.commit("init")
        repo.create_remote("origin", "https://github.com/example/repo.git")
        repo.git.checkout("-b", "feature/parent")

        emitted = []

        class FakeGateway:
            def emit(self, event):
                emitted.append(event)

        result = spawn(
            tasks=[{"goal": "Inspect the repository structure"}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
            wait_for="all_complete",
        )

        assert result.success is True
        event = emitted[0]
        child = event.tasks[0]
        assert child.goal == "Inspect the repository structure"
        assert child.role == "default"
        assert child.params["repo"] == "https://github.com/example/repo.git"
        assert child.sha

    def test_spawn_accepts_legacy_task_and_role_fields(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        with repo.config_writer() as writer:
            writer.set_value("user", "name", "Test Agent")
            writer.set_value("user", "email", "agent@example.com")
        (tmp_path / "README.md").write_text("hello\n")
        repo.index.add(["README.md"])
        repo.index.commit("init")
        repo.create_remote("origin", "https://github.com/example/repo.git")
        repo.git.checkout("-b", "main")

        emitted = []

        class FakeGateway:
            def emit(self, event):
                emitted.append(event)

        result = spawn(
            tasks=[{"task": "Review docs", "role": "default", "branch": "docs-branch"}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        assert result.success is True
        child = emitted[0].tasks[0]
        assert child.goal == "Review docs"
        assert child.role == "default"
        assert child.params["branch"] == "docs-branch"

    def test_spawn_accepts_goal_budget_and_role_fn(self, tmp_path):
        repo = git.Repo.init(tmp_path)
        with repo.config_writer() as writer:
            writer.set_value("user", "name", "Test Agent")
            writer.set_value("user", "email", "agent@example.com")
        (tmp_path / "README.md").write_text("hello\n")
        repo.index.add(["README.md"])
        repo.index.commit("init")
        repo.git.checkout("-b", "main")

        emitted = []

        class FakeGateway:
            def emit(self, event):
                emitted.append(event)

        result = spawn(
            tasks=[{"goal": "Implement OAuth2 login endpoint", "role_fn": "implementer", "budget": 0.6}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        assert result.success is True
        child = emitted[0].tasks[0]
        assert child.goal == "Implement OAuth2 login endpoint"
        assert child.role == "implementer"
        assert child.budget == 0.6
