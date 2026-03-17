from __future__ import annotations

import sys

import click
from loguru import logger

from palimpsest.config import JobConfig
from palimpsest.runner import run_job


@click.group()
def main():
    """Palimpsest — autonomous agent for software engineering tasks."""
    pass


@main.command()
@click.argument("config_file", type=click.Path(exists=True))
@click.option("--verbose", "-v", is_flag=True, help="Enable debug logging")
def run(config_file: str, verbose: bool):
    """Run an agent job from a YAML config file."""
    logger.remove()
    logger.add(sys.stderr, level="DEBUG" if verbose else "INFO")

    config = JobConfig.from_yaml(config_file)
    run_job(config)


@main.command("prompts")
def list_prompts_cmd():
    """List available system prompts."""
    from palimpsest.prompts import list_prompts

    for name in list_prompts():
        click.echo(name)


if __name__ == "__main__":
    main()
