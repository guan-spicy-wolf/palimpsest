import base64

from click.testing import CliRunner

from palimpsest.cli import main


def test_container_entrypoint_runs_job_from_env(monkeypatch):
    runner = CliRunner()
    payload = (
        "job_id: test-job\n"
        "task: test\n"
        "role: default\n"
        "workspace:\n"
        "  repo: https://example.com/repo.git\n"
    )
    monkeypatch.setenv(
        "PALIMPSEST_JOB_CONFIG_B64",
        base64.b64encode(payload.encode("utf-8")).decode("utf-8"),
    )

    seen = {}

    def fake_run_job(config):
        seen["job_id"] = config.job_id
        seen["repo"] = config.workspace.repo

    monkeypatch.setattr("palimpsest.cli.run_job", fake_run_job)

    result = runner.invoke(main, ["container-entrypoint"])

    assert result.exit_code == 0
    assert seen["job_id"] == "test-job"
    assert seen["repo"] == "https://example.com/repo.git"


def test_container_entrypoint_requires_env(monkeypatch):
    runner = CliRunner()
    monkeypatch.delenv("PALIMPSEST_JOB_CONFIG_B64", raising=False)

    result = runner.invoke(main, ["container-entrypoint"])

    assert result.exit_code != 0
    assert "PALIMPSEST_JOB_CONFIG_B64 is not set" in result.output
