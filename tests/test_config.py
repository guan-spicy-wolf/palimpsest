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


def test_job_config_parses_extended_llm_budgets(tmp_path: Path):
    cfg = tmp_path / "job.yaml"
    cfg.write_text(
        "job_id: test-job\n"
        "task: test\n"
        "llm:\n"
        "  max_iterations: 12\n"
        "  max_total_input_tokens: 3456\n"
        "  max_total_output_tokens: 789\n"
        "  max_total_cost: 1.25\n"
    )

    parsed = JobConfig.from_yaml(str(cfg))

    assert parsed.llm.max_iterations == 12
    assert parsed.llm.max_total_input_tokens == 3456
    assert parsed.llm.max_total_output_tokens == 789
    assert parsed.llm.max_total_cost == 1.25
