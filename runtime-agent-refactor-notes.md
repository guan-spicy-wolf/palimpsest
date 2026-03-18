# Runtime / Agent Refactor Notes

本文档整理当前 `agent` 部分需要修改的内容，目标是让代码语义与当前架构意图重新对齐。

适用前提：

- `runtime` 是锁死骨架，负责 orchestration、sandbox、event capture、组件加载。
- `evo` 是可演化仓库，但不再等同于整个 agent 本体。
- `prompt` 是文本资源。
- `context` 与 `tool` 应是运行时约定接口下的具体实现。
- `role` 是便捷模板，不是最终执行语义。
- `spawn(role)` 后应立即展开为基础配置，后续执行只依赖展开结果。
- `event` 的发射由 runtime 保证，不由 agent 主动推动。
- `version management` 如果暂时做不实，先退化为普通 git repo / submodule 读取。

## 1. 需要先统一的核心语义

### 1.1 `role` 的正式定位

当前需要明确：

- `role` 只用于 plan / spawn 阶段的快捷模板。
- `role` 在 job 创建后立即展开。
- job 执行阶段保留的是最基础的配置，而不是 `role` 名称本身。
- `role` 可演化，因此不能作为 runtime 的长期稳定边界。

建议结论：

- `RoleResolver` 的职责应转向“展开模板”而不是“定义运行时核心配置”。
- `ResolvedRole` 更适合被重命名为类似 `ResolvedJobSpec`、`JobSpec` 或等价概念。
- `run_job(...)` 最终应接收基础执行配置，而不是直接围绕 `role` 运转。

### 1.2 当前阶段的版本策略

当前建议明确降级：

- `evo` 暂时只作为普通 git repo / submodule。
- runtime 只读取当前 checkout 内容。
- 暂不承诺 active commit、自动推进、失败回滚。
- 等后续真正具备完整状态机后，再恢复透明版本管理。

## 2. 必须修改的部分

### 2.1 重构 `role -> resolved spec -> runtime` 链路

目标：

- 把 `role` 从 runtime 主入口中降级为“展开时使用的模板”。
- 让 runtime 真正只依赖展开后的基础配置。

当前问题：

- [runner.py](/root/palimpsest/palimpsest/runner.py#L60) 仍然把 `role` 当作直接驱动执行的核心对象。
- [role_resolver.py](/root/palimpsest/palimpsest/runtime/role_resolver.py#L32) 的返回结果语义仍然像“最终角色配置”，不是“展开后的 job spec”。

建议修改：

- 新增或重命名一个更准确的对象，表示“最终执行配置”。
- 把 `prompt`、`context provider spec`、`tool spec`、可能的运行参数都放进这个对象。
- `spawn(role)` 时展开。
- `run_job(...)` 只接受展开后的对象。
- 如果未来允许直接提交完整配置，也应与 `role` 展开后的结构完全兼容。

验收标准：

- 执行主链路不再依赖 `role` 名称本身。
- 同一个 job 即使来源于某个 role，运行时也能只根据展开后的配置复现。

### 2.2 明确 `context` 和 `tool` 的 runtime 接口

目标：

- 让 `context`、`tool` 成为 runtime 认可的稳定扩展点，而不是 YAML 数据壳子。

当前问题：

- 当前 `context` 本质还是 `section type -> if/elif` 的字符串拼接实现。
- 当前 `tool` 虽然在 `role` 中可声明，但运行时没有真正按声明装配。

建议修改：

- 为 `context` 定义稳定接口，例如 provider / renderer 抽象。
- 为 `tool` 定义稳定接口，例如 builtin provider、custom provider、registry 或等价机制。
- runtime 启动时加载完整配置中的 provider 实现。
- `prompt` 保持为简单文本资源，不必过度抽象。

验收标准：

- `context` 与 `tool` 的扩展不再依赖继续向 `runner` 或 stage 中堆硬编码分支。
- runtime 可加载具体 provider 实现，而不是只消费静态 YAML 数据。

### 2.3 移除或冻结假的版本管理承诺

目标：

- 避免代码继续对外表达“已支持透明版本管理”的错误信号。

当前问题：

- [version_manager.py](/root/palimpsest/palimpsest/runtime/version_manager.py#L28) 提供了 `active_sha`、`last_known_good`、`check_for_updates()`、`rollback()`。
- 但 [runner.py](/root/palimpsest/palimpsest/runner.py#L55) 没有形成完整闭环。
- 相关版本事件接口存在，但没有真实生命周期接入。

建议修改：

- 若当前阶段不做完整版本状态机，直接删除或停用 `VersionManager` 的推进/回滚语义。
- CLI 与 runtime 中不要再展示“last known good”这类未兑现概念。
- 文档中明确说明当前只读取 `evo` 当前 checkout。

验收标准：

- 代码中不存在“看起来支持版本推进/回滚，实际上没有”的半成品语义。

### 2.4 收紧 event 边界，保证由 runtime 托管

目标：

- 事件写入和查询都由 runtime 受控。

当前问题：

- [runner.py](/root/palimpsest/palimpsest/runner.py#L125) 直接访问 `gateway._emitter.emit(...)`，绕过了网关边界。
- `recent_events()` 目前是宽泛查询，没有足够的上下文边界。

建议修改：

- 为 stage transition 等事件补充正式的 `EventGateway` 方法。
- 不允许业务代码直接访问底层 `EventEmitter`。
- 查询接口也由 runtime 包装，只暴露受限视图。

验收标准：

- 所有事件写入都经过 `EventGateway`。
- 不再有对 `_emitter` 的直接访问。

### 2.5 修正 `context` 的数据边界

目标：

- 即使实现仍然简单，也要先保证上下文来源干净。

当前问题：

- [context.py](/root/palimpsest/palimpsest/stages/context.py#L44) 的 `recent_events` 是全局最近事件。
- [emitter.py](/root/palimpsest/palimpsest/emitter.py#L56) 只按 `source_id` 查，不按 `job_id` 或 lineage 过滤。

建议修改：

- 至少支持按当前 `job_id` 过滤事件。
- 更理想的方向是引入 lineage / correlation id。
- context builder 不应默认拿到无边界的全局 runtime 历史。

验收标准：

- 并发多个 job 时，agent 不会看到其他 job 的事件。

### 2.6 修复工具沙箱的路径边界

目标：

- 避免路径前缀误判导致越界访问。

当前问题：

- [tools.py](/root/palimpsest/palimpsest/gateway/tools.py#L241) 使用 `startswith()` 做路径校验，存在前缀绕过风险。

建议修改：

- 改为基于真实路径关系的严格包含判断。
- 不要依赖字符串前缀做 sandbox 边界。

验收标准：

- 相邻目录前缀不能绕过 workspace 限制。

## 3. 建议保留但要重新落位的抽象

以下抽象仍值得保留，但要重新落位，避免“接口存在但语义未兑现”：

- `role`
  - 保留为可演化模板。
  - 只服务于 plan / spawn 入口。
  - 不再作为 runtime 主执行语义。

- `context`
  - 保留为可演化扩展点。
  - 但要通过 runtime 认可的 provider 接口接入。

- `tool`
  - 保留为可演化扩展点。
  - 但必须通过 runtime 受控装配。

- `event`
  - 保留为 runtime 锁死边界。
  - 不允许 agent 直接控制发射机制。

## 4. 推荐的修改顺序

推荐按下面顺序推进，避免同时改太多层：

1. 先明确 `role` 的最终语义，并把执行入口改成“吃 resolved spec”。
2. 去掉或冻结当前假的版本管理语义。
3. 为 `context` / `tool` 建立稳定的 runtime 接口。
4. 收紧 event 发射与查询边界。
5. 修正 context 查询过滤。
6. 修复路径沙箱边界。
7. 最后再丰富 context 组装能力与 provider 能力。

## 5. 修改后建议重新 review 的重点

你完成修改后，下一轮 review 建议重点检查：

- `role` 是否真的只在展开阶段存在。
- runtime 是否只依赖基础执行配置。
- `context` / `tool` 接口是否稳定且不过度设计。
- event 是否完全由 runtime 托管。
- 是否已经删掉或明确禁用了假的版本管理语义。
- context 查询是否已具备 job 边界。
- 工具沙箱边界是否严格成立。

## 6. 一句话结论

当前最重要的不是继续叠加功能，而是把以下五个边界重新对齐：

- `role`：模板，不是最终执行语义
- `resolved spec`：runtime 的真实输入
- `context/tool`：runtime 认可的稳定扩展点
- `event`：runtime 托管的锁死边界
- `version policy`：做不实就先降级，不保留半闭环承诺
