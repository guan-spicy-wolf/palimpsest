# Runtime Tightening Changelog

Date: 2026-03-19

## Scope

This changelog records the runtime control-flow and guardrail changes made after the architecture/code review of the current project.

The focus of this round was:

- tightening job completion semantics
- making runtime failures explicit through events
- allowing publication failures to re-enter the same job loop
- making publication recovery configurable
- capturing current known gaps after the changes

## Completed Changes

### 1. Explicit completion semantics

Files:

- `palimpsest/stages/interaction.py`
- `tests/test_interaction_terminal.py`

Changes:

- Only an explicit `task_complete` tool call can terminate the interaction loop successfully.
- Non-`task_complete` tools returning `terminal=True` are ignored by the runtime and no longer end the job.
- If the model stops producing tool calls, the runtime sends one follow-up `user` prompt requesting an explicit `task_complete`.
- If the model still does not call `task_complete`, the job is marked as `partial`.
- The interaction loop now returns its accumulated message history so the same job can resume later with additional runtime prompts.

### 2. Same-job re-entry after runtime publication issues

Files:

- `palimpsest/runner.py`
- `palimpsest/stages/interaction.py`
- `tests/test_runner_runtime_events.py`

Changes:

- Publication guardrail failures no longer require starting a new job.
- The runner can re-enter the interaction loop within the same job by injecting a new `user` prompt that explains the runtime issue and asks the agent to repair the workspace.
- After repair, the agent must explicitly call `task_complete` again.
- Publication recovery attempts are bounded and now configurable.

### 3. Configurable publication recovery

Files:

- `palimpsest/config.py`
- `test/config.example.yaml`
- `palimpsest/runner.py`
- `tests/test_runner_runtime_events.py`

Changes:

- Added `publication.max_recovery_attempts`.
- The runner now uses configuration instead of a hardcoded recovery constant.
- `0` recovery attempts means publication guardrail failures immediately become fatal.

### 4. Duplicate tool names become explicit job failures

Files:

- `palimpsest/gateway/tools.py`
- `palimpsest/runner.py`
- `tests/test_composite_gateway.py`
- `tests/test_runner_runtime_events.py`

Changes:

- Duplicate tool names are detected before creating the composite tool gateway.
- Duplicate names no longer raise directly from `CompositeToolGateway`.
- The runner emits a runtime issue event and then fails the job in a controlled way.
- This makes duplicate-tool failures visible at the job/event level instead of surfacing as an internal constructor error.

### 5. Runtime issue event type added

Files:

- `palimpsest/events.py`
- `palimpsest/runtime/event_gateway.py`
- `palimpsest/runner.py`

Changes:

- Added `RuntimeIssueData`.
- Added event type `job.runtime.issue`.
- The runtime now emits explicit events for:
  - duplicate tool names
  - publication guardrail failures
  - cleanup failures

### 6. Publication and cleanup guardrails extracted into a dedicated stage module

Files:

- `palimpsest/stages/finalization.py`
- `palimpsest/stages/__init__.py`
- `palimpsest/runner.py`
- `tests/test_finalization.py`

Changes:

- Added `find_publication_issues()` for publication preflight checks.
- Added `finalize_workspace_after_job()` for end-of-job cleanup.
- Cleanup no longer only logs warnings: it returns an explicit issue string which is turned into a runtime issue event by the runner.
- Publication guardrails currently detect:
  - tracked secret-like filenames such as `.env`
  - key/certificate-like filenames such as `.pem`, `.key`, `.p12`, `.pfx`
  - PEM private key material in small text files

## Behavioral Result

After this round, the runtime behaves as follows:

- jobs only complete successfully through explicit `task_complete`
- publication problems are surfaced as runtime events
- one publication repair round can happen in the same job by default
- duplicate tool names become controlled job failures
- cleanup problems are surfaced explicitly instead of being silently buried in logs

## Verification

Command run:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run --python 3.11 python -m pytest -q
```

Result:

- `37 passed`

## Review round 2 — fixes applied

### R1. Machine-readable codes on RuntimeIssueData

Files:

- `palimpsest/events.py`
- `palimpsest/runner.py`

Changes:

- Added `code: str = ""` field to `RuntimeIssueData`.
- All three emission sites now carry stable codes:
  - `"duplicate_tool_name"` — duplicate tool names detected at startup
  - `"publication_guardrail"` — publication preflight check failure
  - `"cleanup_failed"` — workspace cleanup failure

### R2. Redundant git import in `_log_evo_checkout`

File:

- `palimpsest/runner.py`

Change:

- `_log_evo_checkout` was importing `git as _git` locally despite `git` already being a top-level import. Removed the redundant local import.

### R3. Python version pin

Files:

- `pyproject.toml`
- `.python-version`

Changes:

- Added `<3.14` upper bound to `requires-python` — pydantic/litellm are not yet compatible with Python 3.14 RC.
- Pinned `.python-version` to 3.11.

### R4. Cosmetic: config.py blank lines

File:

- `palimpsest/config.py`

Change:

- Removed stray blank line between `WorkspaceConfig` and `LLMConfig`.

## Remaining Gaps

These items are still open after this round.

### 1. LLM message normalization is still thin

File:

- `palimpsest/gateway/llm.py`

Status:

- The runtime still feeds provider-returned `raw_message` structures back into later LLM calls with minimal normalization.
- This is likely to become a compatibility problem when models/providers differ in message shape.

### 2. Publication guardrails are still intentionally narrow

File:

- `palimpsest/stages/finalization.py`

Status:

- The current checks do not yet cover:
  - large generated artifacts
  - binary files
  - submodule dirtiness
  - branch policy checks
  - allowlist/blocklist patterns

### 3. Tool naming remains a convention, not a hard architecture rule

Files:

- `evo/tools/*`
- `palimpsest/gateway/tools.py`

Status:

- Tool identity is still defined by `ToolSpec.name`.
- There is not yet a formal decision on whether the long-term direction should be:
  - one file per tool
  - provider namespaces like `file_ops.read_file`
  - another enforced naming scheme

### 4. Publication still stages all changes

File:

- `palimpsest/stages/publication.py`

Status:

- `git add -A` still matches the current one-shot sandbox assumption.
- If the runtime later evolves toward more complex workspaces, staging policy may need to become configurable rather than relying mostly on `.gitignore`.

## Suggested Next Steps

Recommended next implementation targets:

1. Expand publication guardrails into a configurable rule set.
2. Add an LLM message normalization layer before replaying assistant/tool messages across iterations.
3. Decide and enforce a long-term tool naming rule.
