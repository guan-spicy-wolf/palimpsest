# ADR-006: Code-Defined Agent Architecture (代码化 Agent 架构)

## Status

Accepted

## Context

早期版本的 `palimpsest` 中，Agent 的 Role (角色)、Prompt (提示词) 和 Tool (工具) 严重依赖 YAML 配置文件定义。这导致了三个核心痛点：
1. **类型校验缺失**：YAML 配置极易因为拼写错误或格式嵌套错误导致 Agent 启动失败或运行中崩溃，大模型在为其新增特性时缺乏类型提示。
2. **逻辑表达受限**：复杂的 Tool 执行逻辑和多步 Context 获取无法在声明式的 YAML 中表达，导致扩展受限。
3. **黑盒依赖组件**：原有的 LLM Gateway 采用了 `litellm`，隐藏了底层具体 API （如 Anthropic 的 `system` 拆分逻辑或 OpenAI 的 `tool_choice`）的差异，同时内置的 `EventGateway` 存在严重的职责耦合，不利于 `evo` 层独立演进。

## Decision

1. **废弃 YAML 采用纯代码定义 (Code-Defined Roles)**：所有的 Role、Context Provider 和 Tool 均使用纯 Python 模块。通过引入原生的 `@tool` 和 `@context_provider` 装饰器，直接使用 Python 函数签名自动生成 JSON Schema，Role 定义由 `RoleDefinition` 数据类显式约束。为了保持核心模块命名高度统一一致，相关处理模块命名为复数形式（如 `roles.py`、`tools.py`、`contexts.py`）。
2. **重构 LLM Gateway (Native SDKs)**：废弃 `litellm`，重写 `UnifiedLLMGateway`。直接接入原生的 `openai` 和 `anthropic` Python SDK，并在 Gateway 内部针对不同模型的结构化化差异进行精细路由和转换，以确保 Tool Calling 及 Prompt Caching 等高级特性的 100% 兼容。
3. **Pydantic 事件总线 (Event Gateway)**：将杂乱的 `emit_xxx` 方法精简为统一接口 `emit(event: BaseEvent)`。所有的事件采用 `pydantic.BaseModel` 以获得原生 JSON 序列化和校验能力。此外，切断了 `evo` 层对 EventGateway 的直接引用，实行严格的领域隔离和读写分离。

## Consequences

- 彻底消除了因配置语法错误而导致的运行时故障。
- 极大增强了 `palimpsest` 扩展底层 LLM 高级特性的能力。
- 架构的读写分离及隔离边界使得外部的 `evo` 层开发者（即使是大模型本身）修改独立文件时既安全又高效。
