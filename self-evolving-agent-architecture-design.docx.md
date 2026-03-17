  
**Self-Evolving Agent System**

架构设计文档

Architecture Design Document

Draft v0.1

2026-03

# **1  系统概述**

本系统是一个基于 Git 仓库与事件流的自我演化 Agent 系统。其核心目标是让 Agent 在完成对外任务的过程中，发现自身代码的不足，并通过修改自身代码实现持续演化。

系统的两个核心演化方向为：

* 上下文重组能力：如何更高效地从事件流中筛选和组装与当前任务相关的上下文

* 工作流编排能力：如何更好地分配不同的 prompt 以及编排互相调用的工作流

系统仅有两个真实数据来源：Git 仓库和事件流。所有其他状态（包括 Supervisor 的编排状态）均为派生数据，可从事件流重建，也可在内存中缓存以加速。

# **2  核心组件**

## **2.1  事件流（Event Stream）**

事件流是系统的唯一持久化真实来源（与 Git 仓库并列）。无论实例何时重启或内存丢失，新实例总可以从事件流中恢复全部状态。所有组件（Agent、Supervisor）均被视为状态机，状态变更必然对应事件。

### **事件信封结构**

| 字段 | 说明 |
| :---- | :---- |
| id | 事件唯一标识 |
| source | 事件来源（Agent、Supervisor、CI、外部 Client 等） |
| ts | 时间戳 |
| type | 事件类型 |
| data | 事件负载，包含因果关系字段 |

因果关系字段（如 job\_id、commit\_sha、workflow\_instance\_id 等）嵌入在 data 中，而非顶层字段。这是因为不同来源的事件其因果关系形态各异：CI 事件关联的是 commit SHA 和 job id，Supervisor 事件关联的是 workflow 实例，外部 Client 事件可能带的是请求 id。统一的顶层 correlation\_id 会导致语义不清。

### **领域表提取（Projection）**

EventStore 将事件 data 中的关键字段提取到专门的领域表，用于高效查询和统计。这些领域表是派生数据，可丢弃重建。Supervisor 重建工作流状态时查询领域表而非遍历原始事件流。

**约束：**每种 event type 的 data 中，因果关系字段的命名和语义在该类型内保持稳定。EventStore 写入层对已知 event type 做轻量 schema validation，检查必要字段的存在性。此验证属于基础设施层，不在 Agent 自我演化的范围内。

### **事件来源范围**

事件流接收以下来源的事件：

* Agent Job：LLM request/response、tool execution/result、阶段转换、Job 完成/失败

* Supervisor：启动/暂停/恢复/中止、spawn/trigger/resume

* CI/CD：构建触发、测试结果、部署状态

* 外部 Client：任务请求、人类反馈、外部系统触发

## **2.2  Git 仓库**

Git 仓库是另一个真实数据来源，承载代码、配置和 Agent 自身定义。分支模型提供了天然的隔离和回滚边界。

**分支策略：**每个 Agent Job 在独立分支上工作，产出在合并前均可丢弃。这避免了一次性 Agent Loop 的结果污染主干。

## **2.3  Agent**

Agent 是任务执行的核心单元。每个 Agent Job 接收输入仓库/分支和任务目标，经过上下文重组后进入 Agent Loop，最终输出结果事件和可选的新分支。

Agent 具备以下能力：

* 给自己发任务的 tool（通过 Supervisor 的 fork-join 原语实现）

* 定义不同 prompt 的能力（直接嵌入 Agent 自身代码）

* 修改自身代码实现自我演化

## **2.4  Supervisor**

Supervisor 是系统的编排层，负责监听事件并调起 Agent Job。它的编排知识从事件流重建，在内存中缓存为 Projection。

Supervisor 的核心职责：

* 接收事件并调起 Agent Job

* 管理 fork-join 的 spawn/trigger/resume 生命周期

* 多次 Job 启动失败后暂停并上报

* Enforce Agent 的权限边界（不只是约定，而是显式检查）

# **3  权限模型**

权限模型是系统安全性的基础不变量。

| 操作对象 | 权限 |
| :---- | :---- |
| Agent 自身代码 | 直接修改（在分支上） |
| 其他组件（EventStore、Projection、Supervisor 等基础设施） | 仅可提 PR，不可直接修改 |
| 事件 Schema Validation | 基础设施层，修改需走 PR |

**设计理由：**Agent 对自身以外的组件只有建议权，没有执行权。这自然地将“自我演化”和“基础设施演化”分成了两个速率不同的通道。Agent 可以高频地迭代自身代码，但基础设施变更有人类或合并策略把关。此权限边界由 Supervisor 层显式 enforce，而非仅依赖约定。

# **4  工作流原语**

## **4.1  Fork-Join**

系统的工作流原语为 fork-join \+ failure trigger。这是唯一的编排原语，所有复杂编排通过组合此原语实现。

基本流程：

* 发起者 spawn 一组子任务

* Trigger 条件：all complete 或 any failed → resume 发起者

* 发起者 resume 后自行决定下一步（可再次 fork）

### **表达能力**

此原语可表达以下结构：

* **顺序执行：**spawn \[A\]，wait complete，resume 后 spawn \[B\]

* **并行执行：**spawn \[A, B\]，wait all complete，resume

* **条件分支：**Agent 在 resume 后根据子任务结果决定下一步操作

* **嵌套：**被 spawn 的 Job 可以自己再次 fork

* **迭代：**resume 后可再次 spawn，形成多轮 fork-join

### **简化依据**

纯 fork-join 的表达能力是 series-parallel graph，不是任意 DAG。但在本系统中，Job 的边界按仓库切分：同仓库的顺序工作在单 Job 内部解决，跨仓库的工作天然并行。跨仓库的顺序依赖通过父级 resume 后再 spawn 实现。因此不需要更复杂的 DAG 编排。

## **4.2  Job 生命周期**

每个 Agent Job 只有成功和失败两种终态。

**正常路径：**Job 中的每个阶段都会发出事件（细化到每个 LLM request/response 和 tool execution/result）。Job 完成后发出完成事件。

**失败路径：**某个位置超时或失败时，首先尝试重试。无法恢复时，Agent 自身的机制保证工作区提交 WIP（Work In Progress），然后由 follow-up 事件继续尝试闭环。

**Supervisor 层保护：**多次 Job 启动失败后，Supervisor 暂停启动新 Job 并上报。

# **5  上下文重组**

Agent 启动 Job 时，面对的是一个仓库快照加上可能很长的事件流。上下文重组是从事件流中筛选和组装与当前任务相关的信息，填充 LLM 的 context window。

## **5.1  初期策略**

初期提供预定义的上下文模板，同时允许 Agent 在 Loop 中通过 tool 进一步查询事件流。

## **5.2  演化机制**

Agent 在 Loop 中的额外查询被视为信号：它说明初始上下文模板没有预见到这个需求。这些额外查询会被记录为事件，累积后触发专门的优化任务。

**粒度区分：**同一类额外查询在多个 Job 中反复出现，才值得修改模板；仅出现一次的偶发查询，记录但不动作。此频次判断可从事件流的领域表中统计得出。

**优先级：**优化任务为低优先级，批量执行。攻够一批信号后统一分析，避免每次额外查询都立即触发优化 Job。

# **6  自我演化闭环**

## **6.1  演化路径**

Agent 在完成对外任务时发现自身不足，通过修改自身代码实现演化。修改在独立分支上进行，经过验证后合并。

演化方向包括：

* 上下文重组模板的优化

* Prompt 策略的调整

* 工作流编排逻辑的改进

* Tool 使用模式的优化

## **6.2  验证机制（双层 Gate）**

演化采用双层 Gate 设计：

**硬 Gate（立即启用）：**新版本代码必须通过 CI 和 smoke test，且新版本 Job 能正常启动。跳不起来则立即回退。这覆盖了大部分场景，因为当前阶段演化步幅足够小，修改要么正确要么直接崩溃，很少出现“能跑但质量退化”的中间地带。

**软 Gate（先记录，后启用）：**每个 Job 完成时记录可量化指标（额外查询次数、LLM 调用轮数、任务完成耗时、外部产出质量等）。这些指标不立即用作 Gate，但数据已在事件流中积累。当观察到第一次“能跑但变差了”的案例时再启用，届时可做新旧版本的 A/B 对比。

## **6.3  评估锚点**

为避免 Goodhart’s Law（Agent 演化出“让自己觉得自己变好了”的代码而非真正变好），系统需要不在演化循环内的评估锚点。

评估来源包括：

* **自动化测试：**硬指标，可靠但覆盖面窄。始终启用。

* **外部产出质量：**Agent 对外任务的实际产出，作为演化效果的客观衡量。

* **LLM 交叉 Review：**按需启用，评分 prompt 本身不在演化范围内（充当“宪法层”）。

* **人类反馈：**延迟高但最可信，按需引入。

# **7  系统不变量**

以下不变量贯穿系统设计，不应被 Agent 的自我演化破坏：

* **真实来源唯一性：**Git 仓库和事件流是仅有的两个真实数据来源，其他均为派生

* **权限边界：**Agent 对自身以外的组件只有建议权（PR），无执行权

* **事件 Schema 稳定性：**同一 event type 内字段命名和语义保持稳定，Schema 变更属于基础设施演化

* **Job 终态确定性：**每个 Job 必然以成功或失败终结，不存在无限挂起

* **分支隔离：**Job 产出在合并前不影响主干

# **8  初始工作流模式**

初始预想的工作流模式为 plan-exec 协作。但在架构层面，plan-exec 被视为 Agent 代码里的第一个可演化的 workflow 定义，而非架构层的硬约束。

架构只知道“Agent 会发出某种结构的事件来描述自己的 workflow”，至于这个 workflow 是 plan-exec 还是其他什么，是 Agent 代码层面的事。这样既有了起步的具体模式，又没有在架构上锁死。

# **9  实施建议**

建议先用最简单的场景把整个闭环跑通，然后逐步加入复杂度。

**Phase 0 — 最小闭环：**一个固定 prompt 的 Agent，接收一个简单任务（如修改一个文件），产出事件和分支，Supervisor 完成合并。不涉及自我演化，不涉及工作流编排。

**Phase 1 — 上下文重组：**加入预定义模板和事件流查询 tool，验证 Agent 能从事件流中有效获取上下文。

**Phase 2 — Fork-Join：**加入 Supervisor 的 fork-join 编排，Agent 可以 spawn 子任务。

**Phase 3 — 自我演化：**Agent 开始修改自身代码，启用硬 Gate。记录软 Gate 指标。

**Phase 4 — 优化闭环：**启用上下文重组优化任务，根据需要启用软 Gate。

*— End of Document —*