from __future__ import annotations

import sys
from pathlib import Path

import click
from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.runner import run_job


@click.group()
def main():
    """Palimpsest — self-evolving autonomous agent system."""
    pass


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
@click.option("--role", "-r", default=None, help="Override role name")
def run(config_file: str, verbose: bool, role: str | None):
    """Run an agent job from a YAML config file."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")

    config = JobConfig.from_yaml(config_file)
    if role:
        config.role = role
    run_job(config)


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
