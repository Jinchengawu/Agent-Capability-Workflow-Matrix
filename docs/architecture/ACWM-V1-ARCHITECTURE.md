# ACWM v1 架构设计

## 1. 设计目标

ACWM v1 验证一个具体命题：同一个强 Agent Capability 能否在不重写 Agent 内核的前提下，进入两个语义不同的 Workflow Mode，并在跨模式交接、人工暂停、服务重启和代码副作用存在时保持身份、状态与产物连续性。

v1 只验证一条代码交付 Journey，不试图成为新的通用工作流引擎。

## 2. 术语与职责

| 概念 | 职责 | 明确不负责 |
| --- | --- | --- |
| `CapabilityDescriptor` | 声明稳定身份、版本、ACP 启动配置与权限策略 | Journey 状态与工作流循环 |
| `CapabilitySession` | 将一次 Stage/Mode 执行绑定到 Hermes ACP Session | 跨模式共享原始历史 |
| `WorkflowMode` | 命名一种协作/执行语义 | Agent 的 Skills、Tools 或 Memory |
| `CapabilityTransportAdapter` | 翻译 ACP 进程、Session、Prompt、事件、权限与取消 | 阶段路由与模式内部循环 |
| `WorkflowAdapter` | 实现 Direct 或 LangGraph 的执行、Checkpoint 与恢复语义 | Journey 级路由 |
| `ResolvedNode` | 固化某 Stage 的 Capability、Workflow 与策略版本 | 动态改变绑定 |
| `NodeAttempt` | 以不可变值对象表达某 Stage 的一次执行及状态机快照 | 从终态回到 running |
| `NodeRequest/NodeResult` | 统一 Workflow Adapter 的输入、输出、Artifact、指标、错误与证据 | 模式内部状态图 |
| `JourneyDefinition` | 声明有序 Node Step 与 Approval Gate | DAG、并行和 Journey 级循环 |
| `HandoffEnvelope` | 传递版本化、带哈希的必要上下文与 Artifact 引用 | 复制完整对话或私有记忆 |

Direct 与 `langgraph.code-delivery` 都是 `WorkflowMode`：前者对应无 checkpoint、不可 resume 的单次规划调用；后者对应带 checkpoint 的交付循环。Gate 守护的是从已落盘计划 Artifact 到交付 Stage 的转换；Gate revision 只保护该审批对象，Journey revision 则标识整个控制面 Snapshot 的演进。

## 3. 六边形结构

```text
domain       不可变契约、状态与定义
application  Journey 用例、审批、权限、恢复与 Artifact 协调
ports        CapabilityTransport 等外部边界
adapters     Hermes ACP、LangGraph、SQLite、Git、Artifact Store
api          FastAPI REST/SSE 与幂等控制面
```

`domain` 不依赖 FastAPI、LangGraph、ACP、SQLite 或 Git。基础设施只通过 Port 进入应用层。

## 4. 参考 Journey

### 4.1 Direct 规划

ACWM 创建隔离 worktree 和 Direct Session，要求 Hermes 检查仓库并输出实施计划。规划阶段沿用 Capability 的 worktree 权限策略；计划进入内容寻址 Artifact Store，并以其 SHA-256 打开 `approve-plan` Gate。

### 4.2 审批与 Handoff

审批命令必须同时携带 Gate revision 和计划 hash。任何旧版本审批都会返回 `409 stale_decision`。批准后生成不可变 `HandoffEnvelope`，其中只包含目标、计划摘要、约束、事实、来源 Attempt 和 Artifact 引用。

### 4.3 LangGraph 交付

交付 Stage 使用新的 mode-scoped Hermes Session。LangGraph 内部包含：

```text
implement → verify → review ──accepted──→ END
                     └─repair────────────→ verify
```

验证命令使用 argv 数组执行，不经过 shell。测试失败或 Review 拒绝时最多修复两轮；耗尽预算后 Stage 失败并保留 worktree。

Implement、Review 与 Repair 都调用同一个 Hermes Capability，并在一次 Attempt 内复用同一个逻辑 Session；Review 不是独立 reviewer Agent。

## 5. 三类真相源

| 真相源 | 保存内容 |
| --- | --- |
| `acwm.sqlite` | Journey、Stage、Gate、Attempt、权限、幂等命令、事件与 Snapshot |
| `langgraph.sqlite` | LangGraph thread/checkpoint 和模式内部恢复游标 |
| Git worktree | 实际代码状态、diff 与未提交修改 |

Snapshot 是查询缓存，可由事件表中携带的最新完整 `snapshot_json` 重建；v1 尚未实现纯语义事件 reducer。LangGraph Checkpoint 不得反向覆盖 ACWM 已完成 Stage。SQLite 不保存代码正文，只保存 Artifact hash、URI 与 lineage。

## 6. 恢复语义

ACWM 不声称 exactly-once，因为 Event Commit、LangGraph Checkpoint、ACP 工具副作用与 Git 写入不处于同一事务。

| 对象 | 恢复时的身份语义 |
| --- | --- |
| Stage | Journey Definition 中的稳定节点 |
| Attempt | Stage 的一次执行；resume/retry 都追加新 Attempt 与 lineage |
| Capability Session | 限定于 `journey + stage + workflow mode`；同进程内可复用，ACP 进程重启后新建实际 Session |
| Checkpoint thread | resume 复用旧 thread，retry 创建新 thread；从不恢复 Hermes 原始对话 |

- 服务重启时，仍为 `running` 的 Attempt 变为 `interrupted`，Journey 进入 `needs_attention`。
- `resume` 创建新 Attempt，设置 `resumes_attempt_id`，并沿旧 LangGraph checkpoint thread 继续。
- `retry` 创建新 Attempt，设置 `retries_attempt_id`，从 Stage 入口重新执行。
- Direct 不支持 resume，只允许 retry。
- 已完成 Stage 不自动重放。
- `resume` 复用旧 checkpoint thread，但服务重启后会创建新的 Hermes ACP Session，不能恢复原始对话；`retry` 使用新的 checkpoint thread，并从 Stage 入口重新执行。
- 取消先向运行中的 ACP Session 发出 cancel，再以一个 SQLite 事务持久化 Journey、Attempt、Stage 与 Gate 的取消状态；无论结果如何都保留 worktree。
- v1 使用单进程、单 Uvicorn worker 与单 worktree writer。

## 7. 权限模型

Hermes ACP 权限请求先经过 Capability 的静态策略：

- 只读、搜索和思考类操作可直接允许。
- 文件编辑仅在所有目标路径都位于受管 worktree 且策略允许时放行。
- ACP 工具命令与 API 提交的 `verification_commands` 都必须匹配 Capability 的显式前缀白名单；verification subprocess 以受管 worktree 为 cwd，不经过 shell。
- 其他请求持久化为 `permission.required`，Journey 进入 `awaiting_permission`，通过 REST 决策后继续。

事件和 API 响应对名称包含 secret、password、token、api-key 的字段进行脱敏。非 loopback API 必须配置 Bearer Token。

## 8. 外部 API

所有 mutation 都要求 `Idempotency-Key`。请求正常完成后，同 Key/同 Body 返回原响应；同 Key/不同 Body 返回 `409`。业务 mutation 与幂等响应记录不是跨组件单事务，因此不构成 exactly-once 保证。SSE Event 使用持久化单调 ID，支持 `Last-Event-ID` 重放，客户端断开不会取消 Journey。

公开资源包括 Capability、Workflow Mode、Journey、Gate Decision、Permission Decision、Cancel、Resume、Retry 和 Event Stream。

## 9. v1 边界

Open-Agent-Teams 仍是未来消费者，不是 ACWM v1 的依赖。v1 不包含额外编排框架、团队 Agent、应用 UI、共享长期记忆、远程执行、多租户、分布式队列、A2A/MCP 外部协议或自动 merge/push/PR。
