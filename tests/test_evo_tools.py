from pathlib import Path

import git
import httpx
from yoitsu_contracts.config import ToolsConfig

from palimpsest.runtime.tools import UnifiedToolGateway, create_pr, resolve_tool_functions, spawn

EVO_ROOT = Path(__file__).parent.parent / "evo"


class TestFileOps:
    def test_read_file(self, tmp_path):
        (tmp_path / "hello.txt").write_text("world")
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["read_file"])
        result = funcs["read_file"](path="hello.txt", workspace=str(tmp_path))
        assert result.success
        assert "world" in result.output

    def test_read_file_not_found(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["read_file"])
        result = funcs["read_file"](path="nope.txt", workspace=str(tmp_path))
        assert not result.success

    def test_write_file(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["write_file"])
        result = funcs["write_file"](path="new.txt", content="hello", workspace=str(tmp_path))
        assert result.success
        assert (tmp_path / "new.txt").read_text() == "hello"

    def test_write_file_creates_dirs(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["write_file"])
        result = funcs["write_file"](path="sub/dir/f.txt", content="nested", workspace=str(tmp_path))
        assert result.success
        assert (tmp_path / "sub" / "dir" / "f.txt").read_text() == "nested"

    def test_list_files(self, tmp_path):
        (tmp_path / "a.txt").write_text("a")
        (tmp_path / "b.txt").write_text("b")
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["list_files"])
        result = funcs["list_files"](path=".", workspace=str(tmp_path))
        assert result.success
        assert "a.txt" in result.output
        assert "b.txt" in result.output

    def test_list_files_not_a_dir(self, tmp_path):
        funcs = resolve_tool_functions(EVO_ROOT, "default", ["list_files"])
        result = funcs["list_files"](path="nonexistent", workspace=str(tmp_path))
        assert not result.success

def test_task_complete_tool_is_removed():
    funcs = resolve_tool_functions(EVO_ROOT, "default", ["task_complete"])
    assert funcs == {}


def test_unified_tool_gateway_treats_builtin_tools_as_builtin(monkeypatch):
    requested = []

    def fake_resolve_tool_functions(_evo_root, _bundle, names):
        requested.append(list(names))
        return {}

    monkeypatch.setattr("palimpsest.runtime.tools.resolve_tool_functions", fake_resolve_tool_functions)

    class FakeGateway:
        def emit(self, _event):
            return None

    gateway = UnifiedToolGateway(
        config=ToolsConfig(),
        evo_root=EVO_ROOT,
        bundle="",
        requested_evo_tools=["spawn", "create_pr", "read_file"],
        gateway=FakeGateway(),
    )

    assert requested == [["read_file"]]
    schemas = gateway.schema()
    assert any(item["function"]["name"] == "spawn" for item in schemas)
    assert any(item["function"]["name"] == "create_pr" for item in schemas)


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
            tasks=[{"goal": "Inspect the repository structure", "role": "default"}],
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
        # repo is now a top-level field, not in params
        assert child.repo == "https://github.com/example/repo.git"
        assert child.sha

    def test_spawn_rejects_legacy_task_field(self, tmp_path):
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
            tasks=[{"task": "Review docs", "role": "default"}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        # Legacy field 'task' should be rejected
        assert result.success is False
        # Check that it's rejected because of the legacy field, not because of missing goal
        assert "legacy" in result.output.lower() or "not allowed" in result.output.lower() or "forbidden" in result.output.lower()

    def test_spawn_rejects_legacy_branch_field(self, tmp_path):
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
            tasks=[{"goal": "Review docs", "role": "default", "branch": "docs-branch"}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        # Legacy field 'branch' should be rejected
        assert result.success is False
        assert "legacy" in result.output.lower() or "not allowed" in result.output.lower()

    def test_spawn_accepts_goal_budget_and_role(self, tmp_path):
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
            tasks=[{"goal": "Implement OAuth2 login endpoint", "role": "implementer", "budget": 0.6}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        assert result.success is True
        child = emitted[0].tasks[0]
        assert child.goal == "Implement OAuth2 login endpoint"
        assert child.role == "implementer"
        assert child.budget == 0.6

    def test_spawn_accepts_params_for_role_internal_flags(self, tmp_path):
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
            tasks=[{"goal": "Join and review", "role": "planner", "params": {"mode": "join"}}],
            workspace=str(tmp_path),
            gateway=FakeGateway(),
            evo_root=str(EVO_ROOT),
        )

        assert result.success is True
        child = emitted[0].tasks[0]
        assert child.goal == "Join and review"
        assert child.role == "planner"
        assert child.params == {"mode": "join"}


def test_create_pr_calls_github_api(monkeypatch):
    captured = {}

    def fake_post(url, *, headers, json, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["json"] = json

        class FakeResponse:
            is_success = True
            status_code = 201

            def json(self):
                return {
                    "html_url": "https://github.com/example/repo/pull/1",
                    "url": "https://api.github.com/repos/example/repo/pulls/1",
                    "number": 1,
                    "title": "Test PR",
                    "body": "This is a test PR",
                    "head": {"ref": "feature/branch"},
                    "base": {"ref": "main"},
                    "state": "open",
                }

        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")

    result = create_pr(
        repo="https://github.com/example/repo.git",
        head_branch="feature/branch",
        base_branch="main",
        title="Test PR",
        body="This is a test PR",
    )

    assert result.success
    assert "github.com" in captured["url"]
    assert captured["json"]["title"] == "Test PR"
    assert captured["json"]["head"] == "feature/branch"
    assert captured["json"]["base"] == "main"