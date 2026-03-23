from pathlib import Path

from palimpsest.config import JobConfig


def test_job_config_accepts_publication_strategy(tmp_path: Path):
    cfg = tmp_path / "job.yaml"
    cfg.write_text(
        "job_id: test-job\n"
        "task: test\n"
        "publication:\n"
        "  strategy: branch\n"
        "  branch_prefix: palimpsest/job\n"
    )

    parsed = JobConfig.from_yaml(str(cfg))

    assert parsed.publication.strategy == "branch"
    assert parsed.publication.branch_prefix == "palimpsest/job"
