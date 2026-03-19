from unittest.mock import patch

import git

from palimpsest.stages.finalization import (
    finalize_workspace_after_job,
    find_publication_issues,
)


def test_finalize_workspace_after_job_returns_issue_on_failure(tmp_path):
    with patch("palimpsest.stages.finalization.shutil.rmtree", side_effect=OSError("boom")):
        issue = finalize_workspace_after_job(str(tmp_path))
    assert issue is not None
    assert "boom" in issue


def test_find_publication_issues_detects_sensitive_file(tmp_path):
    repo = git.Repo.init(tmp_path)
    secret = tmp_path / ".env"
    secret.write_text("TOKEN=secret")
    repo.index.add([".env"])
    repo.index.commit("add secret")

    issues = find_publication_issues(repo)
    assert issues == ["Sensitive-looking file tracked: .env"]
