from __future__ import annotations

import os
import shutil
from pathlib import Path

import git
from loguru import logger

from palimpsest.events import RuntimeIssueData
from palimpsest.runtime.event_gateway import EventGateway


def finalize_workspace_after_job(
    workspace_path: str,
    gateway: EventGateway | None = None,
    *,
    keep_env: str = "PALIMPSEST_KEEP_WORKSPACE",
) -> str | None:
    """Best-effort cleanup for one-shot sandbox jobs.

    When *gateway* is provided, emits a runtime-issue event on failure
    so the runner does not have to.
    """
    if os.environ.get(keep_env, "").strip() in {"1", "true", "yes"}:
        logger.info(f"Keeping workspace due to {keep_env}=1: {workspace_path}")
        return None

    try:
        shutil.rmtree(workspace_path)
        logger.info(f"Cleaned up workspace: {workspace_path}")
        return None
    except Exception as exc:
        message = f"Failed to clean up workspace {workspace_path}: {exc}"
        logger.warning(message)
        if gateway:
            gateway.emit(
                RuntimeIssueData(
                    stage="cleanup",
                    fatal=False,
                    code="cleanup_failed",
                    error=message,
                )
            )
        return message


def find_publication_issues(
    repo: git.Repo,
    *,
    allow_sensitive_env: str = "PALIMPSEST_ALLOW_SENSITIVE",
) -> list[str]:
    """Return a list of publication issues (guardrails).

    Current policy is intentionally minimal: only flags common secret-like
    filenames and private key material. Set allow_sensitive_env=1 to bypass.
    """
    if os.environ.get(allow_sensitive_env, "").strip() in {"1", "true", "yes"}:
        return []

    root = Path(repo.working_tree_dir or ".")
    tracked = repo.git.ls_files().splitlines()

    sensitive_suffixes = (".pem", ".key", ".p12", ".pfx")
    sensitive_basenames = {
        ".env",
        ".pypirc",
        ".npmrc",
        "id_rsa",
        "id_ed25519",
        "credentials",
    }

    issues: list[str] = []
    for rel in tracked:
        p = Path(rel)
        name = p.name
        if name in sensitive_basenames or name.startswith(".env."):
            issues.append(f"Sensitive-looking file tracked: {rel}")
            continue
        if any(name.endswith(s) for s in sensitive_suffixes):
            issues.append(f"Key/cert-looking file tracked: {rel}")
            continue

        # Quick content sniff for PEM headers in small-ish text files.
        abs_path = root / rel
        try:
            if abs_path.is_file() and abs_path.stat().st_size <= 128_000:
                head = abs_path.read_text(errors="ignore")[:4000]
                if "BEGIN PRIVATE KEY" in head or "BEGIN RSA PRIVATE KEY" in head:
                    issues.append(f"Private key material detected in: {rel}")
        except Exception:
            # Non-fatal: ignore unreadable files.
            pass

    return issues
