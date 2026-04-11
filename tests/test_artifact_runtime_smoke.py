"""Phase 4 Step 4: Non-Git task smoke path verification (ADR-0013).

This test verifies that the artifact runtime adoption is complete:
- Input artifacts can be materialized in preparation
- Output artifacts are created in publication
- The entire flow works without git dependency
"""

import os
import shutil
import tempfile
from pathlib import Path

import pytest

from palimpsest.events import JobCompletedData
from palimpsest.stages.preparation import _materialize_input_artifacts
from palimpsest.stages.publication import create_artifact_bindings
from yoitsu_contracts.artifact import ArtifactBinding
from yoitsu_contracts.local_fs_backend import LocalFSBackend


def test_non_git_artifact_roundtrip():
    """ADR-0013 Phase 4 Step 4: Full artifact roundtrip without Git.

    Flow:
    1. Create temp LocalFSBackend store
    2. Pre-write input tree artifact
    3. Materialize input to workspace (preparation step)
    4. Create output artifact bindings (publication step)
    5. Re-materialize output and verify content
    """
    dirs_to_cleanup = []

    try:
        # 1. Setup temp artifact store
        store_root = tempfile.mkdtemp(prefix="artifact-store-")
        dirs_to_cleanup.append(store_root)
        backend = LocalFSBackend(Path(store_root))

        # 2. Create input tree artifact
        input_dir = tempfile.mkdtemp(prefix="input-content-")
        dirs_to_cleanup.append(input_dir)
        (Path(input_dir) / "README.md").write_text("# Input Artifact\n\nThis is test content.")
        (Path(input_dir) / "src").mkdir()
        (Path(input_dir) / "src" / "main.py").write_text("print('hello')")

        input_tree_ref = backend.store_tree(Path(input_dir))
        input_binding = ArtifactBinding(
            ref=input_tree_ref,
            relation="input",
            path="",  # materialize at workspace root
            metadata={"description": "test input tree"},
        )

        # 3. Materialize input artifacts (preparation step)
        test_workspace = tempfile.mkdtemp(prefix="test-materialize-")
        dirs_to_cleanup.append(test_workspace)

        # Set artifact store env for _materialize_input_artifacts
        old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
        os.environ["PALIMPSEST_ARTIFACT_STORE"] = store_root

        _materialize_input_artifacts([input_binding], test_workspace)

        # Verify input files were materialized
        assert (Path(test_workspace) / "README.md").exists()
        assert (Path(test_workspace) / "src" / "main.py").exists()
        assert (Path(test_workspace) / "README.md").read_text() == "# Input Artifact\n\nThis is test content."

        # 4. Modify workspace and create output artifact bindings
        (Path(test_workspace) / "output.txt").write_text("Generated output")
        (Path(test_workspace) / "src" / "generated.py").write_text("# Generated file")

        # Create output bindings using artifact store
        output_bindings = create_artifact_bindings(
            workspace_path=test_workspace,
            artifact_store_root=Path(store_root),
        )

        # Verify output bindings created
        assert len(output_bindings) > 0, "Expected artifact bindings to be created"

        # Find the output tree binding
        output_tree_binding = None
        for b in output_bindings:
            if b.relation == "output" and b.ref.object_kind == "tree":
                output_tree_binding = b
                break

        assert output_tree_binding is not None, "Expected output tree binding"

        # 5. Re-materialize output and verify
        output_workspace = tempfile.mkdtemp(prefix="test-output-")
        dirs_to_cleanup.append(output_workspace)

        backend.materialize_tree(output_tree_binding.ref, Path(output_workspace))

        # Verify output can be re-materialized
        assert (Path(output_workspace) / "output.txt").exists()
        assert (Path(output_workspace) / "output.txt").read_text() == "Generated output"
        assert (Path(output_workspace) / "src" / "generated.py").exists()
        assert (Path(output_workspace) / "src" / "main.py").exists()  # Original input preserved

        # 6. Verify no git_ref dependency - entire flow used no Git
        # The artifact_bindings carry the full output representation

        # Restore env
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store
        else:
            os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

    finally:
        # Cleanup temp dirs
        for d in dirs_to_cleanup:
            if d and Path(d).exists():
                shutil.rmtree(d, ignore_errors=True)


def test_artifact_binding_in_job_completed_event():
    """Verify JobCompletedData carries artifact_bindings from publication."""
    store_root = tempfile.mkdtemp(prefix="artifact-store-")
    backend = LocalFSBackend(Path(store_root))

    workspace = tempfile.mkdtemp(prefix="test-workspace-")
    (Path(workspace) / "result.txt").write_text("Job output")

    bindings = create_artifact_bindings(
        workspace_path=workspace,
        artifact_store_root=Path(store_root),
    )

    # Create JobCompletedData with bindings
    event = JobCompletedData(
        git_ref=None,  # No Git
        summary="Completed with artifacts",
        artifact_bindings=bindings,
    )

    assert event.artifact_bindings == bindings
    assert len(event.artifact_bindings) > 0
    assert event.git_ref is None  # No Git dependency

    # Cleanup
    shutil.rmtree(store_root, ignore_errors=True)
    shutil.rmtree(workspace, ignore_errors=True)


def test_blob_artifact_roundtrip():
    """Test single blob artifact input/output."""
    store_root = tempfile.mkdtemp(prefix="artifact-store-")
    backend = LocalFSBackend(Path(store_root))

    # Create input blob
    input_content = b"Input blob content\nwith multiple lines"
    input_blob_ref = backend.store_blob(input_content)
    input_binding = ArtifactBinding(
        ref=input_blob_ref,
        relation="input",
        path="data/input.bin",
    )

    # Materialize blob using the same store
    workspace = tempfile.mkdtemp(prefix="blob-workspace-")

    old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
    os.environ["PALIMPSEST_ARTIFACT_STORE"] = store_root

    try:
        _materialize_input_artifacts([input_binding], workspace)

        # Verify blob materialized
        blob_path = Path(workspace) / "data" / "input.bin"
        assert blob_path.exists()
        assert blob_path.read_bytes() == input_content

        # Create output blob
        output_content = b"Output blob content"
        output_blob_ref = backend.store_blob(output_content)
        output_binding = ArtifactBinding(
            ref=output_blob_ref,
            relation="output",
            path="data/output.bin",
        )

        # Verify can retrieve
        retrieved = backend.retrieve_blob(output_blob_ref)
        assert retrieved == output_content

    finally:
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store
        else:
            os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

        shutil.rmtree(store_root, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)


def test_artifact_store_env_variable():
    """Verify PALIMPSEST_ARTIFACT_STORE is used by _materialize_input_artifacts."""
    store_root = tempfile.mkdtemp(prefix="custom-store-")
    backend = LocalFSBackend(Path(store_root))

    # Create artifact
    input_dir = tempfile.mkdtemp(prefix="input-")
    (Path(input_dir) / "file.txt").write_text("content")
    ref = backend.store_tree(Path(input_dir))
    binding = ArtifactBinding(ref=ref, relation="input", path="")

    workspace = tempfile.mkdtemp(prefix="ws-")

    old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
    os.environ["PALIMPSEST_ARTIFACT_STORE"] = store_root

    try:
        _materialize_input_artifacts([binding], workspace)

        # Verify file exists in workspace
        assert (Path(workspace) / "file.txt").exists()
        assert (Path(workspace) / "file.txt").read_text() == "content"

    finally:
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store
        else:
            os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

        shutil.rmtree(store_root, ignore_errors=True)
        shutil.rmtree(input_dir, ignore_errors=True)
        shutil.rmtree(workspace, ignore_errors=True)


def test_git_publication_returns_artifacts_for_repoless_workspace():
    """P1 fix: git_publication() must return artifact_bindings for repoless workspace."""
    from palimpsest.runtime.roles import git_publication

    workspace = tempfile.mkdtemp(prefix="repoless-ws-")
    (Path(workspace) / "output.txt").write_text("repoless output")

    pub_fn = git_publication()
    result = {"status": "complete", "summary": "test"}

    # Set artifact store so bindings can be created
    store_root = tempfile.mkdtemp(prefix="pub-store-")
    old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
    os.environ["PALIMPSEST_ARTIFACT_STORE"] = store_root

    try:
        git_ref, artifact_bindings = pub_fn(
            result=result,
            workspace_path=workspace,
            job_id="test-repoless",
            task_id="test-repoless",
            goal="Test repoless publication",
        )

        # P1 fix verification: no git_ref, but artifact_bindings should exist
        assert git_ref is None, "Expected no git_ref for repoless workspace"
        assert len(artifact_bindings) > 0, "Expected artifact_bindings for repoless workspace"

        # Verify binding has proper structure
        output_binding = artifact_bindings[0]
        assert output_binding.relation == "output"
        assert output_binding.ref.object_kind == "tree"

    finally:
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store
        else:
            os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(store_root, ignore_errors=True)


def test_default_store_root_consistency():
    """P1 fix: publication and preparation use same default store root."""
    # Without PALIMPSEST_ARTIFACT_STORE set, both should default to ~/.cache/palimpsest/artifacts

    # Create a workspace with output
    workspace = tempfile.mkdtemp(prefix="default-store-ws-")
    (Path(workspace) / "file.txt").write_text("content")

    # Clear env to test default
    old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
    os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

    try:
        # Create bindings (publication side)
        bindings = create_artifact_bindings(workspace_path=workspace)

        # Verify bindings were created
        assert len(bindings) > 0

        # Materialize to new workspace (preparation side)
        new_workspace = tempfile.mkdtemp(prefix="materialize-ws-")
        _materialize_input_artifacts(bindings, new_workspace)

        # Verify content roundtrips with default store
        assert (Path(new_workspace) / "file.txt").exists()
        assert (Path(new_workspace) / "file.txt").read_text() == "content"

    finally:
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store

        shutil.rmtree(workspace, ignore_errors=True)
        shutil.rmtree(new_workspace, ignore_errors=True)


def test_artifact_materialization_after_clone():
    """P1 fix: artifacts materialize after clone, not before."""
    # This test would fail before the fix if both repo and input_artifacts were set
    # because git clone would fail on non-empty directory.

    store_root = tempfile.mkdtemp(prefix="clone-test-store-")
    backend = LocalFSBackend(Path(store_root))

    # Create input artifact
    input_dir = tempfile.mkdtemp(prefix="clone-input-")
    (Path(input_dir) / "config.yaml").write_text("key: value")
    ref = backend.store_tree(Path(input_dir))
    binding = ArtifactBinding(ref=ref, relation="input", path="")

    old_store = os.environ.get("PALIMPSEST_ARTIFACT_STORE")
    os.environ["PALIMPSEST_ARTIFACT_STORE"] = store_root

    src_repo_dir = None
    workspace = None

    try:
        # Create a temp git repo to clone from
        import git
        src_repo_dir = tempfile.mkdtemp(prefix="src-repo-")
        src_repo = git.Repo.init(src_repo_dir)
        # Explicitly create and checkout master branch (default for git init)
        (Path(src_repo_dir) / "README.md").write_text("source repo")
        src_repo.index.add(["README.md"])
        src_repo.index.commit("initial")
        # Get actual branch name
        actual_branch = src_repo.active_branch.name

        # Run preparation with both repo AND input_artifacts
        # Before fix: clone fails because workspace isn't empty after materialization
        # After fix: clone succeeds, then artifacts overlay on top
        from palimpsest.config import WorkspaceConfig
        from palimpsest.stages.preparation import run_preparation

        config = WorkspaceConfig(
            repo=src_repo_dir,  # Local repo for test
            init_branch=actual_branch,  # Use actual branch from repo
            new_branch=False,
            input_artifacts=[binding],
        )

        workspace = run_preparation(
            job_id="test-clone-with-artifacts",
            config=config,
            task_id="test",
            goal="Test clone + artifacts",
        )

        # Verify both clone content and artifact content exist
        assert (Path(workspace) / "README.md").exists()  # From clone
        assert (Path(workspace) / "config.yaml").exists()  # From artifact

    finally:
        if old_store:
            os.environ["PALIMPSEST_ARTIFACT_STORE"] = old_store
        else:
            os.environ.pop("PALIMPSEST_ARTIFACT_STORE", None)

        shutil.rmtree(store_root, ignore_errors=True)
        shutil.rmtree(input_dir, ignore_errors=True)
        if src_repo_dir:
            shutil.rmtree(src_repo_dir, ignore_errors=True)
        if workspace:
            shutil.rmtree(workspace, ignore_errors=True)


def test_spawn_tool_accepts_input_artifacts():
    """P1 fix: spawn tool must accept and pass input_artifacts."""
    from palimpsest.runtime.tools import _normalize_spawn_task

    store_root = tempfile.mkdtemp(prefix="spawn-tool-store-")
    backend = LocalFSBackend(Path(store_root))

    # Create artifact
    input_dir = tempfile.mkdtemp(prefix="spawn-input-")
    (Path(input_dir) / "file.txt").write_text("spawn content")
    ref = backend.store_tree(Path(input_dir))

    try:
        # Create task dict with input_artifacts
        task_dict = {
            "goal": "Test spawn with artifacts",
            "role": "worker",
            "input_artifacts": [{
                "ref": ref.model_dump(mode="json"),
                "relation": "input",
                "path": "data",
            }],
        }

        # Normalize the task
        normalized = _normalize_spawn_task(task_dict, workspace="/tmp/test", evo_sha="")

        assert len(normalized.input_artifacts) == 1
        assert normalized.input_artifacts[0].ref.digest == ref.digest
        assert normalized.input_artifacts[0].path == "data"

    finally:
        shutil.rmtree(store_root, ignore_errors=True)
        shutil.rmtree(input_dir, ignore_errors=True)