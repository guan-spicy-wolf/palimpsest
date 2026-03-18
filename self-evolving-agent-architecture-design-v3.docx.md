  
**Self-Evolving Agent System**

架构设计文档

Architecture Design Document

Draft v0.3

2026-03

# **1  系统概述**

本系统是一个基于 Git 仓库与事件流的自我演化 Agent 系统。其核心目标是让 Agent 在完成对外任务的过程中，发现自身的不足，并通过修改可演化内容实现持续演化。

系统的两个核心演化方向为：

* 上下文重组能力：如何更高效地从事件流中筛选和组装与当前任务相关的上下文

* 工作流编排能力：如何更好地分配不同的 Prompt 以及编排互相调用的工作流

系统仅有两个真实数据来源：Git 仓库和事件流。所有其他状态（包括 Supervisor 的编排状态、可演化组件的活跃版本）均为派生数据，可从事件流重建。

架构的核心设计原则是将 Agent 拆分为不可变的骨架（Runtime）和可自由演化的肌肉（可演化仓库）。可演化内容存储在一个专用的 Git 仓库中，Agent 的演化操作与做普通任务完全一致——都是在分支上修改文件、提交、等待合并。版本化由 Runtime 在 Git 层面透明完成，Agent 无需感知额外的版本管理机制。

# **2  核心组件**

## **2.1  事件流（Event Stream）**

事件流是系统的唯一持久化真实来源（与 Git 仓库并列）。无论实例何时重启或内存丢失，新实例总可以从事件流中恢复全部状态。所有组件（Agent、Supervisor）均被视为状态机，状态变更必然对应事件。

### **事件信封结构**

| 字段 | 说明 |
| :---- | :---- |
| id | 事件唯一标识 |
| source | 事件来源（Agent Runtime、Supervisor、CI、外部 Client 等） |
| ts | 时间戳 |
| type | 事件类型 |
| data | 事件负载，包含因果关系字段 |

因果关系字段（如 job\_id、commit\_sha、workflow\_instance\_id 等）嵌入在 data 中，而非顶层字段。不同来源的事件因果关系形态各异：CI 事件关联 commit SHA 和 job id，Supervisor 事件关联 workflow 实例，外部 Client 事件可能带请求 id。统一的顶层 correlation\_id 会导致语义不清。

### **领域表提取（Projection）**

EventStore 将事件 data 中的关键字段提取到专门的领域表，用于高效查询和统计。这些领域表是派生数据，可丢弃重建。Supervisor 重建工作流状态时查询领域表而非遍历原始事件流。

**约束：**每种 event type 的 data 中，因果关系字段的命名和语义在该类型内保持稳定。EventStore 写入层对已知 event type 做轻量 schema validation。此验证属于基础设施层，不在演化范围内。

### **事件来源范围**

* Agent Runtime：LLM request/response、tool execution/result、阶段转换、Job 完成/失败（透明事件网关自动捕获）

* Agent 业务决策：spawn 请求（通过稳定 Tool 接口显式发出）

* Runtime 版本管理：可演化仓库的版本推进、回退事件

* Supervisor：启动/暂停/恢复/中止、spawn/trigger/resume

* CI/CD：构建触发、测试结果、部署状态

* 外部 Client：任务请求、人类反馈、外部系统触发

## **2.2  Git 仓库**

系统中涉及两类 Git 仓库：

* **任务仓库：**外部任务的代码仓库。Agent 在独立分支上工作，产出在合并前均可丢弃。

* **可演化仓库：**存储 Agent 的可演化内容（Prompt、Context 模板、Tool 定义、Role 定义）。Agent 演化自身时操作的就是这个仓库。详见第 3 节。

**分支策略：**两类仓库采用相同的分支模型。每个 Job 在独立分支上工作，经过验证后合并。区别仅在于：可演化仓库的合并会额外触发 Runtime 的版本推进逻辑。

## **2.3  Agent Runtime（骨架）**

Agent Runtime 是不可演化的基础设施，提供 Agent 运行所需的机械性能力。Agent 在沙箱环境中运行，Runtime 控制沙箱边界。

### **Runtime 职责**

* **LLM 调用机制：**发起和管理与 LLM 的通信，包括重试、超时处理

* **Tool 执行框架：**加载和执行 Tool 定义，管理 Tool 的权限边界

* **沙箱环境管理：**提供隔离的运行环境，控制 Agent 对外部资源的访问

* **可演化仓库版本管理：**跟踪活跃 commit、检测合并、执行版本推进和回退（详见第 3 节）

### **透明事件网关**

事件的发送与 Agent 代码完全隔离。Runtime 在 Agent 与 EventStore 之间充当透明网关，自动捕获并发送事件，Agent 代码无需也无法直接操作事件机制。

事件捕获分为两类：

* **Runtime 级事件（完全透明）：**LLM 的 request/response、Tool 的 call/result、Job 的阶段转换、最终的成功/失败状态。由 Runtime 自动捕获，Agent 无感知。

* **业务级事件（通过稳定 Tool）：**spawn 子任务等业务决策。通过 Runtime 提供的固定 Tool 接口显式发出。Tool 接口本身稳定不可修改。

## **2.4  Supervisor**

Supervisor 是系统的编排层，负责监听事件并调起 Agent Job。编排状态从事件流重建，在内存中缓存为 Projection。

Supervisor 的核心职责：

* 接收事件并调起 Agent Job

* 根据 Job 指定的 Role 名称，从可演化仓库的当前活跃 commit 中解析出具体组件，注入 Runtime

* 管理 fork-join 的 spawn/trigger/resume 生命周期

* 多次 Job 启动失败后暂停并上报

* Enforce Agent 的权限边界（显式检查）

# **3  可演化仓库**

Agent 的全部智能内容存储在一个专用的 Git 仓库中。对 Agent 而言，演化自身和做普通任务的心智模型完全一致：在分支上修改文件、提交、等待合并。唯一的区别是这个仓库碰巧是 Agent 自己的“大脑”。

## **3.1  仓库结构**

可演化仓库采用约定的目录结构：

* **prompts/：**Prompt 定义文件。包括 system prompt、任务指令模板等。

* **contexts/：**Context 模板文件。定义从事件流中筛选和组装上下文的规则。

* **tools/：**业务 Tool 定义文件。区别于 Runtime 提供的固定 Tool。

* **roles/：**Role 定义文件。每个 Role 声明它引用的 Prompt、Context 模板和 Tool 的文件路径。

因为所有文件在同一个 commit 下，不存在跨版本兼容性问题——一个 commit 就是一个完整的、自洽的快照。

## **3.2  Role 定义**

Role 是 Prompt、Tool 集合、Context 模板三者的命名组合，通过引用同仓库内的文件路径来声明组成。

Role 可以有继承关系。定义一个 default Role 作为基线，其他 Role 只声明与 default 的差异。大部分 spawn 使用 default Role，只有特殊任务才指定专门的 Role。

## **3.3  版本管理机制**

Git commit SHA 就是版本号，不需要额外的版本管理机制。Runtime 维护一个“活跃 commit”指针，指向当前正在使用的可演化仓库版本。

### **版本推进流程**

Agent 在可演化仓库的分支上修改文件并提交。分支经过 CI 和 smoke test 后合并到 main。Runtime 检测到 main 的 HEAD 变化后，diff 出变更的文件，发出版本推进事件（包含 old\_sha、new\_sha、changed\_files），然后将活跃 commit 推进到新的 HEAD。

### **回退机制**

如果新 commit 下第一个 Job 启动失败，Runtime 自动将活跃 commit 回退到上一个已知可用的 SHA，并发出回退事件。“上一个已知可用的 SHA”即最近一个成功完成 Job 时使用的 commit，此信息可从事件流重建。

### **Job 启动时的加载**

Supervisor 调起 Job 时，以当前活跃 commit 检出可演化仓库，读取 Job 指定的 Role 文件，解析出具体的 Prompt、Context 模板和 Tool 定义，注入 Runtime 沙箱。Job 只加载 Role 声明的组件，不是全量加载。

## **3.4  设计优势**

* **统一心智模型：**Agent 演化自身和做普通任务的操作方式完全一致，不需要学习额外的注册 API

* **原子快照：**同一 commit 下的所有组件天然自洽，不存在版本不匹配问题

* **零额外 Tool：**无需 register\_prompt、register\_role 等注册 Tool，版本化完全由 Runtime 在 Git 层面透明完成

* **低冲突率：**不同演化任务通常改的是不同文件，Git 层面自动合并

* **可追溯性：**Git 的 commit 历史 \+ 事件流中的版本推进事件，提供完整的演化可追溯性

* **精确对比：**因为版本推进事件包含 changed\_files，可以从领域表中查询“某个 Prompt 修改后，使用该 Prompt 的 Job 指标变化”

# **4  权限模型**

权限模型是系统安全性的基础不变量，分为三层。

| 层级 | 内容 | 权限 |
| :---- | :---- | :---- |
| 锁死层 | Runtime 代码、透明事件网关、沙箱环境、EventStore、Schema Validation、版本推进/回退逻辑 | Agent 完全不可触碰。修改需走 PR。 |
| 稳定层 | spawn 等业务 Tool 接口、可演化仓库的目录结构约定 | Agent 可使用但不可修改。约定变更需走 PR。 |
| 自由层 | 可演化仓库中的所有文件（Prompt、Context 模板、Tool 定义、Role 组合） | Agent 通过 Git 操作自由演化。版本由 Runtime 透明管理。 |

对外部任务仓库的权限不变：Agent 对自身以外的组件只有建议权（PR），无执行权。Supervisor 层显式 enforce。

# **5  工作流原语**

## **5.1  Fork-Join**

系统的工作流原语为 fork-join \+ failure trigger。这是唯一的编排原语，所有复杂编排通过组合此原语实现。

基本流程：

* 发起者 spawn 一组子任务，指定 Role 名称和任务参数

* Trigger 条件：all complete 或 any failed → resume 发起者

* 发起者 resume 后自行决定下一步（可再次 fork）

### **表达能力**

* **顺序执行：**spawn \[A\]，wait complete，resume 后 spawn \[B\]

* **并行执行：**spawn \[A, B\]，wait all complete，resume

* **条件分支：**Agent 在 resume 后根据子任务结果决定下一步

* **嵌套：**被 spawn 的 Job 可以自己再次 fork

* **迭代：**resume 后可再次 spawn，形成多轮 fork-join

### **简化依据**

Job 的边界按仓库切分：同仓库的顺序工作在单 Job 内部解决，跨仓库的工作天然并行。跨仓库的顺序依赖通过父级 resume 后再 spawn 实现。因此纯 fork-join 已足够，不需要更复杂的 DAG 编排。

## **5.2  Job 生命周期**

每个 Agent Job 只有成功和失败两种终态。

**正常路径：**透明事件网关自动捕获每个阶段的事件。Job 完成后发出完成事件。

**失败路径：**超时或失败时先重试。无法恢复时提交 WIP，由 follow-up 事件继续尝试闭环。

**Supervisor 层保护：**多次 Job 启动失败后暂停并上报。

# **6  上下文重组**

Agent 启动 Job 时，面对一个仓库快照和可能很长的事件流。上下文重组的规则由 Role 指定的 Context 模板定义，存储在可演化仓库的 contexts/ 目录下。

## **6.1  初期策略**

初期提供预定义的 Context 模板，同时允许 Agent 在 Loop 中通过 Tool 进一步查询事件流。

## **6.2  演化机制**

Agent 在 Loop 中的额外查询被视为信号，由透明事件网关自动记录。累积后触发专门的优化任务。优化任务的产出就是修改可演化仓库中的 Context 模板文件，走正常的分支-合并-版本推进流程。

**粒度区分：**同一类额外查询在多个 Job 中反复出现才触发优化；偶发查询记录但不动作。此频次判断从事件流领域表中统计。

**优先级：**优化任务为低优先级，批量执行。攻够一批信号后统一分析。

# **7  自我演化闭环**

## **7.1  演化路径**

Agent 的演化操作就是修改可演化仓库中的文件。演化范围严格限定在可演化仓库内，包括：

* Context 模板的优化（修改 contexts/ 下的文件）

* Prompt 策略的调整（修改 prompts/ 下的文件）

* Role 组合的改进（修改 roles/ 下的文件）

* 业务 Tool 的优化（修改 tools/ 下的文件）

演化产出走正常的分支-CI-合并流程，合并后由 Runtime 自动推进版本。

## **7.2  验证机制（双层 Gate）**

**硬 Gate（立即启用）：**新版本合并后，使用新 commit 的第一个 Job 必须能正常启动并通过 smoke test。失败则 Runtime 自动回退活跃 commit。

**软 Gate（先记录，后启用）：**透明事件网关自动记录 Job 的可量化指标。版本推进事件包含 changed\_files，可从领域表中查询某文件修改前后的 Job 指标变化，实现精确的 A/B 对比。

## **7.3  评估锚点**

为避免 Goodhart’s Law，系统需要不在演化循环内的评估锚点：

* **自动化测试：**硬指标，可靠但覆盖面窄。始终启用。

* **外部产出质量：**Agent 对外任务的实际产出，作为演化效果的客观衡量。

* **LLM 交叉 Review：**按需启用，评分 Prompt 本身不在演化范围内。

* **人类反馈：**延迟高但最可信，按需引入。

# **8  系统不变量**

以下不变量贯穿系统设计，不应被 Agent 的自我演化破坏：

* **真实来源唯一性：**Git 仓库和事件流是仅有的两个真实数据来源。活跃 commit 指针等状态均为事件流的派生

* **骨架/肌肉分离：**Runtime（骨架）不可演化，可演化仓库（肌肉）可自由演化。两者通过 Git 操作 \+ Runtime 透明版本管理连接

* **事件捕获透明性：**Runtime 级事件由透明网关自动捕获，Agent 无法触碰事件发送机制

* **权限三层模型：**锁死层/稳定层/自由层的边界由 Supervisor 显式 enforce

* **事件 Schema 稳定性：**同一 event type 内字段命名和语义保持稳定。Schema 变更属于锁死层

* **Job 终态确定性：**每个 Job 必然以成功或失败终结

* **分支隔离：**Job 产出在合并前不影响主干

# **9  初始工作流模式**

初始预想的工作流模式为 plan-exec 协作。但在架构层面，plan-exec 被视为可演化仓库中的第一个 Role 定义，而非架构层的硬约束。

架构只知道“Agent 会发出某种结构的事件来描述自己的 workflow”，至于具体模式是什么，是 Role 层面的事。随着 Agent 演化，工作流模式也可能被演化为其他形式。

# **10  实施建议**

建议先用最简单的场景把整个闭环跑通，然后逐步加入复杂度。

**Phase 0 — 最小闭环：**Runtime \+ 透明事件网关 \+ 固定 Role。一个硬编码的 Agent，接收简单任务，产出事件和分支，Supervisor 完成合并。验证透明事件捕获和基本流程。

**Phase 1 — 可演化仓库：**建立可演化仓库的目录结构。Prompt 和 Context 模板从硬编码迁移到仓库中。实现 Role 解析、活跃 commit 跟踪、版本推进和回退机制。

**Phase 2 — 上下文重组：**加入事件流查询 Tool，验证 Agent 能从事件流中有效获取上下文。

**Phase 3 — Fork-Join：**加入 Supervisor 的 fork-join 编排，Agent 可以 spawn 子任务并指定 Role。

**Phase 4 — 自我演化：**Agent 开始在可演化仓库的分支上修改文件来演化自身。启用硬 Gate（启动失败自动回退）。开始记录软 Gate 指标。

**Phase 5 — 优化闭环：**启用 Context 模板优化任务。根据需要启用软 Gate 进行版本对比。

*— End of Document —*