from pathlib import Path

from palimpsest.config import JobConfig, PreparationConfig, WorkspaceConfig


def test_preparation_config_is_workspace_config_alias():
    """PreparationConfig is the new name for WorkspaceConfig (ADR-0009 D1)."""
    # PreparationConfig is the canonical name
    prep = PreparationConfig(repo="https://github.com/example/test")
    assert prep.repo == "https://github.com/example/test"
    assert prep.init_branch == "main"
    assert prep.new_branch == True
    
    # WorkspaceConfig is an alias for backward compatibility
    ws = WorkspaceConfig(repo="https://github.com/example/repo")
    assert ws.repo == "https://github.com/example/repo"
    
    # They are the same class
    assert PreparationConfig is WorkspaceConfig


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
    assert parsed.bundle == "default"


def test_job_config_parses_extended_llm_budgets(tmp_path: Path):
    cfg = tmp_path / "job.yaml"
    cfg.write_text(
        "job_id: test-job\n"
        "task: test\n"
        "evo_sha: abc123\n"
        "llm:\n"
        "  max_iterations: 12\n"
        "  max_iterations_hard: 99\n"
        "  iteration_penalty_cost: 0.05\n"
        "  tool_timeout_seconds: 45\n"
        "  max_total_input_tokens: 3456\n"
        "  max_total_output_tokens: 789\n"
        "  max_total_cost: 1.25\n"
    )

    parsed = JobConfig.from_yaml(str(cfg))

    assert parsed.evo_sha == "abc123"
    assert parsed.llm.max_iterations == 12
    assert parsed.llm.max_iterations_hard == 99
    assert parsed.llm.iteration_penalty_cost == 0.05
    assert parsed.llm.tool_timeout_seconds == 45
    assert parsed.llm.max_total_input_tokens == 3456
    assert parsed.llm.max_total_output_tokens == 789
    assert parsed.llm.max_total_cost == 1.25
