"""Tests for tool resolution and builtin tools (Bundle MVP).

The file_ops tests were removed as they relied on global evo/tools/ layer
which no longer exists per Bundle MVP.
"""
from pathlib import Path

import git
import httpx
from yoitsu_contracts.config import ToolsConfig

from palimpsest.runtime.tools import UnifiedToolGateway, create_pr, spawn

EVO_ROOT = Path(__file__).parent / "fixtures" / "evo"


def test_task_complete_tool_is_removed():
    """task_complete tool no longer exists (was removed in earlier refactor)."""
    from palimpsest.runtime.tools import resolve_tool_functions
    funcs = resolve_tool_functions(EVO_ROOT, "", ["task_complete"])
    assert funcs == {}


def test_unified_tool_gateway_treats_builtin_tools_as_builtin(monkeypatch):
    """Builtin tools (spawn, create_pr) are not resolved from evo directory."""
    requested = []

    def fake_resolve_tool_functions(_bundle_workspace, _bundle, names):
        requested.append(list(names))
        return {}

    monkeypatch.setattr("palimpsest.runtime.tools.resolve_tool_functions", fake_resolve_tool_functions)

    class FakeGateway:
        def emit(self, _event):
            return None

    gateway = UnifiedToolGateway(
        config=ToolsConfig(),
        bundle_workspace=EVO_ROOT,
        bundle="",
        requested_evo_tools=["spawn", "create_pr", "read_file"],
        gateway=FakeGateway(),
    )

    # Only read_file should be resolved from evo (spawn/create_pr are builtin)
    assert requested == [["read_file"]]
    schemas = gateway.schema()
    assert any(item["function"]["name"] == "spawn" for item in schemas)
    assert any(item["function"]["name"] == "create_pr" for item in schemas)


class TestSpawn:
    """Tests for spawn builtin tool."""

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
            bundle_workspace=str(EVO_ROOT),
            wait_for="all_complete",
        )

        assert result.success is True
        event = emitted[0]
        child = event.tasks[0]
        assert child.goal == "Inspect the repository structure"
        assert child.role == "default"
        # repo is now a top-level field, not in params
        assert child.repo == "https://github.com/example/repo.git"
        # sha may be None if no HEAD commit exists
        assert child.init_branch == "feature/parent"

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
            bundle_workspace=str(EVO_ROOT),
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
            bundle_workspace=str(EVO_ROOT),
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
            bundle_workspace=str(EVO_ROOT),
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
            bundle_workspace=str(EVO_ROOT),
        )

        assert result.success is True
        child = emitted[0].tasks[0]
        assert child.goal == "Join and review"
        assert child.role == "planner"
        assert child.params == {"mode": "join"}


def test_create_pr_calls_github_api(monkeypatch):
    """Test create_pr builtin tool calls GitHub API correctly."""
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