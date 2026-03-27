from pathlib import Path

import git

from palimpsest.events import JobStartedData
from palimpsest.config import WorkspaceConfig
from palimpsest.stages.workspace import setup_workspace


def test_setup_workspace_configures_git_identity_for_cloned_repo(tmp_path, monkeypatch):
    source = tmp_path / "source"
    repo = git.Repo.init(source)
    with repo.config_writer() as writer:
        writer.set_value("user", "name", "Source User")
        writer.set_value("user", "email", "source@example.com")
    (source / "README.md").write_text("hello\n")
    repo.index.add(["README.md"])
    repo.index.commit("init")

    monkeypatch.setenv("PALIMPSEST_GIT_USER_NAME", "Test Agent")
    monkeypatch.setenv("PALIMPSEST_GIT_USER_EMAIL", "agent@example.com")
    workspace = setup_workspace(
        "job-1",
        WorkspaceConfig(repo=str(source), init_branch="master", depth=1),
        branch_prefix="palimpsest/job",
        task_id="task-abc123",
        goal="Define Shared Graph Contracts",
    )
    cloned = git.Repo(workspace)

    reader = cloned.config_reader(config_level="repository")
    assert reader.get_value("user", "name") == "Test Agent"
    assert reader.get_value("user", "email") == "agent@example.com"
    assert cloned.active_branch.name == "palimpsest/job/task-abc123/define-shared-graph-contracts"


def test_setup_workspace_configures_default_git_identity_for_empty_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PALIMPSEST_GIT_USER_NAME", "Test Agent")
    monkeypatch.setenv("PALIMPSEST_GIT_USER_EMAIL", "agent@example.com")

    workspace = setup_workspace(
        "job-2",
        WorkspaceConfig(repo="", init_branch="main"),
        branch_prefix="palimpsest/job",
    )
    assert not (Path(workspace) / ".git").exists()


def test_setup_workspace_emits_job_started_with_cost_tracking_state(tmp_path):
    events = []

    class FakeGateway:
        def emit(self, event):
            events.append(event)

    workspace = setup_workspace(
        "job-3",
        WorkspaceConfig(repo="", init_branch="main"),
        branch_prefix="palimpsest/job",
        gateway=FakeGateway(),
        cost_tracking_degraded=True,
    )

    assert workspace
    started = [event for event in events if isinstance(event, JobStartedData)]
    assert started
    assert started[-1].cost_tracking_degraded is True
