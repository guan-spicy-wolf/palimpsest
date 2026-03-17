from __future__ import annotations

from pathlib import Path


PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """Load system prompt by name. Resolves: name → prompts/{name}.md"""
    path = PROMPTS_DIR / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {name}")
    return path.read_text()


def list_prompts() -> list[str]:
    """List available prompt names (without .md extension)."""
    if not PROMPTS_DIR.exists():
        return []
    return [p.stem for p in PROMPTS_DIR.glob("*.md")]
