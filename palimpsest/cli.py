from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

import click
from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.runner import run_job


@click.group()
def main():
    """Palimpsest — self-evolving autonomous agent system."""
    pass


def _configure_logging(verbose: bool) -> None:
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")


def _run_from_path(config_file: str, *, verbose: bool, role: str | None = None) -> None:
    _configure_logging(verbose)
    config = JobConfig.from_yaml(config_file)
    if role:
        config.role = role
    run_job(config)


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--role", "-r", default=None, help="Override role name")
def run(config_file: str, verbose: bool, role: str | None):
    """Run an agent job from a YAML config file."""
    _run_from_path(config_file, verbose=verbose, role=role)


@main.command("container-entrypoint")
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def container_entrypoint(verbose: bool):
    """Decode container config from the environment and run the job."""
    payload_b64 = os.environ.get("PALIMPSEST_JOB_CONFIG_B64", "")
    if not payload_b64:
        raise click.ClickException("PALIMPSEST_JOB_CONFIG_B64 is not set")

    try:
        payload = base64.b64decode(payload_b64).decode("utf-8")
    except Exception as exc:
        raise click.ClickException(f"Invalid PALIMPSEST_JOB_CONFIG_B64 payload: {exc}")

    with tempfile.TemporaryDirectory(prefix="palimpsest-job-config-") as tmpdir:
        config_path = Path(tmpdir) / "job.yaml"
        config_path.write_text(payload)
        _run_from_path(str(config_path), verbose=verbose)


@main.command("roles")
@click.option("--evo-path", default="evo", help="Path to evolvable repository")
def list_roles_cmd(evo_path: str):
    """List available roles in the evolvable repository."""
    from palimpsest.runtime import RoleResolver

    resolver = RoleResolver(evo_path)
    for name in resolver.list_roles():
        click.echo(name)


@main.command("version")
@click.option("--evo-path", default="evo", help="Path to evolvable repository")
def show_version(evo_path: str):
    """Show the current checkout SHA of the evolvable repository."""
    import git as _git

    try:
        repo = _git.Repo(Path(evo_path))
        click.echo(f"Current checkout: {repo.head.commit.hexsha}")
    except Exception:
        click.echo("Could not read evolvable repo HEAD")


if __name__ == "__main__":
    main()
