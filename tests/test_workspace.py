import git

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
    )
    cloned = git.Repo(workspace)

    reader = cloned.config_reader(config_level="repository")
    assert reader.get_value("user", "name") == "Test Agent"
    assert reader.get_value("user", "email") == "agent@example.com"


def test_setup_workspace_configures_default_git_identity_for_empty_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("PALIMPSEST_GIT_USER_NAME", "Test Agent")
    monkeypatch.setenv("PALIMPSEST_GIT_USER_EMAIL", "agent@example.com")

    workspace = setup_workspace(
        "job-2",
        WorkspaceConfig(repo="", init_branch="main"),
        branch_prefix="palimpsest/job",
    )
    repo = git.Repo(workspace)
    reader = repo.config_reader(config_level="repository")

    assert reader.get_value("user", "name") == "Test Agent"
    assert reader.get_value("user", "email") == "agent@example.com"
