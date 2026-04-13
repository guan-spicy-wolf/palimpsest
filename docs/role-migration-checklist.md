# Role Migration Checklist (ADR-0018)

This document tracks the migration status of all production roles from legacy lifecycle hooks to capability-only model.

## Status Legend

- 🔴 **blocked** - Blocked by ADR-0019 authority split
- 🟡 **pending** - Ready to migrate
- 🟢 **done** - Migration complete

## Role Inventory

| Role | Bundle | Legacy Fields | Authority Issue | Migration Target | Status |
|------|--------|---------------|-----------------|------------------|--------|
| `optimizer` | default | ~~preparation_fn, publication_fn~~ | None | `needs=[]` (empty capability) | 🟢 |
| `optimizer` | factorio | ~~preparation_fn, publication_fn~~ | None | `needs=[]` (empty capability) | 🟢 |
| `worker` | factorio | `preparation_fn` (RCON+mod sync), `publication_fn`, `__publication_strategy__="skip"` | **Yes** - live runtime authority | `factorio_runtime` capability | 🔴 |
| `implementer` | factorio | `preparation_fn` (workspace_override), `publication_fn`, `__publication_strategy__="skip"` | **Yes** - bundle modification authority | bundle capability | 🔴 |
| `evaluator` | factorio | `preparation_fn` (workspace_override), `publication_fn`, `__publication_strategy__="skip"` | **Yes** - bundle validation authority | bundle capability | 🔴 |

## Legacy Fields Being Deprecated

Per ADR-0018, these fields will be removed from role/runtime contract:

- `preparation_fn` - Role-private workspace setup logic
- `publication_fn` - Role-private artifact publication logic
- `__publication_strategy__` - Publication behavior control
- `workspace_override` - Ephemeral workspace bypass

## Migration Path

### Analysis-Only Roles (No Authority Issue)

Roles that only analyze observations and output proposals (no repo modification, no live runtime effect):

1. Remove `preparation_fn` - not needed (no workspace setup)
2. Remove `publication_fn` - not needed (output via summary field)
3. Remove `__publication_strategy__` - not needed
4. Add `needs=[]` to role decorator (explicit empty capability)
5. Verify tests pass with unified lifecycle

### Repo-Authoring Roles

Roles that produce git commits:

1. Remove `preparation_fn` - workspace setup handled by Trenni
2. Remove `publication_fn` - publication handled by `git_workspace` capability
3. Add `needs=["git_workspace"]` to role decorator
4. Verify tests pass

### Live Runtime Roles (Blocked by ADR-0019)

Roles that affect live runtime state (Factorio mod sync, RCON):

1. **Wait for ADR-0019** to determine authority model
2. Create `factorio_runtime` capability for:
   - Mod script sync
   - RCON connection management
   - Cleanup handling
3. Migrate preparation logic to capability
4. Add `needs=["factorio_runtime"]` to role decorator

## Current Status

- **Phase 1 Complete**: Runtime unified to single lifecycle path
- **Phase 2 In Progress**: Role migration started
- **Phase 3 Pending**: Delete legacy fields and helpers

## Task 6 Status: Blocked by ADR-0019

Bundle capabilities prepared but not yet integrated:
- `factorio_runtime` capability skeleton created in `evo/factorio/capabilities/`
- Implements preparation logic from `evo/factorio/lib/preparation.py`
- Integration pending until ADR-0019 determines authority model

## Task 9: Validation Matrix Results

| Item | Description | Status |
|------|-------------|--------|
| V1 | 空 capability role：可执行、可完成、无 fallback | ✅ `test_empty_needs_uses_unified_lifecycle` |
| V2 | builtin capability role：setup/finalize 均经过统一 lifecycle | ✅ `test_needs_git_workspace_uses_capability_path` |
| V3 | repo role：通过 git_workspace capability 完成持久化语义 | ✅ `test_git_workspace_capability_*` |
| V4 | bundle runtime role：在 authority 明确后，通过 bundle capability 完成 lifecycle | ⏳ Blocked by ADR-0019 |
| V5 | grep/validation：生产 role 中不再出现 legacy 字段（除 blocked） | ✅ Verified |
| V6 | runtime code：不存在按 needs 选择 legacy path 的主分支 | ✅ Verified |

## Completion Criteria

ADR-0018 视为完成，当：
1. ✅ runtime 不再按 needs 在 capability path 与 legacy path 之间二选一（主分支）
2. ✅ 非 blocked 生产 role 不再通过 role-level lifecycle hooks 定义执行模型
3. ✅ builtin capability 承担 setup/finalize 责任
4. ✅ legacy lifecycle contract 在非 blocked role 中不可用
5. ✅ 空 capability role 作为统一 lifecycle 的正常特例稳定工作
6. ⏳ Blocked roles 待 ADR-0019 后完成迁移

## ADR-0018 Status: **Phased Complete**

Phase 1-3 已完成，Phase 4 验收通过。
Blocked roles (factorio:worker, factorio:implementer, factorio:evaluator) 待 ADR-0019 authority split 后完成最终迁移。