# TOOLING-MCP-CONTRACT.md — TS-1 Tooling & MCP Contract（DESIGN）

> **工单**：TS-1（DESIGN）
> **状态**：**DESIGN ONLY — 只设计，0 code / 0 MCP server / 0 production**
> **前置**：TS-0 审计（`docs/TOOLING-MCP-AUDIT.md`，审计结论直接采信，不重开）
> **产出**：本文档（唯一产出物；未创建/修改任何 source、test、config、data 文件）
> **性质**：非施工授权。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准；本文档是 TS-2（IMPLEMENTATION）的输入。
> **设计者**：developer（专责 bot）
> **设计日期**：2026-09-04
> **工作区**：`C:\Users\bbfcc\.local\bin\soul-os-harness`（`pwsh pwd` 确认）

---

## 0. 摘要（TL;DR）

Soul OS 的感知路径（World → EventBus → WorldPerceptionMiddleware → prompt）与意志路径（Motive → Decision 四元 → transmit）均已闭环，但**工具执行路径存在结构性缺口**（TS-0 确认）：observe/reflect 是「空转」决策（`src/soul/motive.py:29-30` 明示执行逻辑是后续工单），`observe_environment` capability 有声明无兑现，MCP 接入为零。

本设计把 TS-0 的方案 B（分组聚合）+ Tooling Volition Gate + 权限分级/安全降级落成**四大锁定契约**，作为 TS-2 实作的单一事实依据：

1. **tool_registry.py 接口规范**（§2）：独立动态注册表，MCP Tool 动态注册 + 健康检查 + 自动归类至 observe / reflect / communicate 三大能力组；capability.py 0 改动。
2. **Tooling Volition Gate 契约**（§3）：Decision 批准 → Actuator 派发单次调用 → 结果回流 World Context / Perception；**0 自主递归**（严禁工具内部自主递归自激，不做无脑 ReAct 循环）。
3. **权限分级与安全降级**（§4）：唯读感知类 Auto-Approved；敏感变更类 Ask-Required；外部 MCP Server 断线/超时 Fail-closed 平滑降级至空结果或预设缓存，**绝不阻塞主心跳**。
4. **冻结契约审查**（§5）：SAGE / EventBus / Agency 4-stage 零代码修改，0 CONTRACT CONFLICT。

**正式原则（继承 CA-1 / SM-1）**：`Capability makes an action conceivable; Motive makes it desirable; Decision makes it chosen.` MCP 工具注册后只产生**能力声明**（「你可以感知外部环境（天气、日历、新闻、搜索）」），不预设「你应该现在查天气」；工具调用与 transmit 同权，全走 Motive + Decision 批准。

---

## 1. 背景与前置（TS-0 采信摘要）

| 项 | 内容 |
|---|---|
| TS-0 核心结论 | 外部 API 调用全部硬编码（Open-Meteo / iCal / RSS / Fish TTS 四处）；observe/reflect 空转；MCP 接入为零；Volition Gate 已有强地基（`_decision_check` fail-closed + WorldEventSource 无权 publish AGENT_SPEAK + SI-2.1 防线 1）；沙盒与权限守门为零 |
| 方案（已锁） | **方案 B（分组聚合）**：新增 `src/soul/tool_registry.py` 动态注册表 → MCP 工具分组聚合映射为 CapabilityDefinition（observe_environment / communicate / reflect_memory 三组）→ 执行器挂在 Decision 四元之下 → 全部工具调用过 Volition Gate |
| 聚合原则 | **Decision 只看 3 个能力组，不看 N 个工具**——工具明细只在执行器层展开（执行时按 Motive 内容路由到具体工具），避免 Decision 提示词膨胀 |
| 复用防线 | 防线 1 感知隔离（WorldEventSource 只能 emit WorldEvent）/ 防线 2 意志闸门（`_decision_check` fail-closed）/ 防线 3 社交 Ambient——MCP 工具接入**复用而非绕过**这三道防线 |
| 不改 | Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE / EventBus / capability.py / decision.py / motive.py / scheduler 职责 |

**已核对的真实代码事实**（本设计引用，未改动）：

- `src/soul/capability.py:64-77`：`CAPABILITY_DEFINITIONS` 静态 3 组（communicate / observe_environment / reflect_memory），`project_capabilities` + `format_capability_block` 只读投影，fail-silent，deterministic。
- `src/soul/motive.py:29-30`：SM-4 四元 transmit/observe/reflect/do_nothing；observe/reflect 的执行逻辑是后续工单，scheduler 层面 observe/reflect/do_nothing 均不 publish → rejected。
- `src/soul/scheduler.py:372-422`（`_decision_check`）：只消费 `result.transmit`，observe/reflect 选择后直接 `mark_rejected`——**Decision 选了 observe，没有任何机制去实际读天气/日历**。
- `src/soul/decision.py:58`：注释「observe / reflect 的执行逻辑（读天气/读日记）是后续工单, 本模块只做选择」。
- `src/world/base.py:13-18`：WorldEventSource ABC 硬约束——source 不得直接取得 EventBus / 不得 publish AGENT_INTENT / AGENT_SPEAK / 不得取得 SpeakerToken / 不得呼叫 LLM / 不得写 Memory / SAGE / Diary / Dream。**感知源被类型系统隔离在行动之外**。
- `src/llm/fish_tts_handler.py:417-460`：`_call_fish_api_blocking` 直接 `requests.post` Fish Audio API（Bearer token，timeout 180s），失败返回 None 不 raise。
- `src/world/middleware.py:196-836`：WorldPerceptionMiddleware 三段式（Pass 1 评分 → Pass 2 top-N → Pass 3 trace）后 re-publish `AGENT_INTENT_PERCEIVED`（payload 带 world_context）→ LLMProxy 注入 prompt。

---

## 2. 契约 1：tool_registry.py 接口规范

### 2.1 模块定位与边界

- **新增** `src/soul/tool_registry.py`（TS-2 实作），独立动态注册表。
- **capability.py 0 改动**：`CAPABILITY_DEFINITIONS` 常数保持静态；动态注册表在投影时**合并**静态定义 + 动态注册（方案 B，TS-0 §2.3）。
- **不碰 frozen contract**：不 import `src/work/roles.py`（DSH ROLE_CAPABILITIES 隔离，CA-1 Q7 死规则）；不改 Agency / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE / EventBus。
- **可观测性**：注册/注销/健康状态变化记录进自有 sidecar（append-only，独立 schema，与 `capability_projection_trace.jsonl` 模式对齐），并打 logger.info。

### 2.2 接口签名（设计示意，非实现代码）

```python
# src/soul/tool_registry.py（设计示意，TS-2 实现）

@dataclass(frozen=True)
class RegisteredTool:
    tool_id: str            # 唯一身份：f"{server_id}:{name}"（复合，防跨 server 冲突）
    server_id: str          # 所属 MCP server 的 id
    name: str               # MCP tools/list 返回的工具名
    description: str        # 工具描述（来自 MCP schema，用于自动归类）
    input_schema: dict      # MCP JSON Schema（tools/call 参数校验用）
    capability_group: str   # 自动归类结果：observe_environment | communicate | reflect_memory
    permission_class: str   # 权限分级：auto_approved | ask_required（§4.1）
    health: str             # healthy | degraded | offline（§2.4）

@dataclass(frozen=True)
class ToolResult:
    ok: bool                # 调用是否成功
    data: Any               # 成功时的结构化结果（observe → 感知数据；reflect → 记忆摘要）
    error: str | None       # 失败原因（超时 / 断线 / 异常 / 权限拒绝）
    degraded: bool          # 是否来自降级路径（空结果 / 预设缓存，§4.2）
    cached: bool            # 是否来自预设缓存（staleness 由调用方标注）

class ToolRegistry:
    # ── 动态注册 / 注销 ──
    def register_mcp_server(self, server_id: str, client) -> list[RegisteredTool]:
        """连接 MCP server → tools/list → 逐工具自动归类（§2.3）→ 注册。
        任一工具无法归类 → 该工具拒绝注册（fail-closed，§2.3.3），server 其余工具照常注册。"""
    def unregister_mcp_server(self, server_id: str) -> None:
        """注销整个 server 的工具（断线 / 主动移除）。注销后该组工具不再投影。"""

    # ── 查询 ──
    def list_tools(self, group: str | None = None) -> list[RegisteredTool]:
        """按能力组过滤列出已注册工具（group=None 列出全部）。"""
    def get_tool(self, tool_id: str) -> RegisteredTool | None:
        """按 tool_id 取单个工具（Actuator 路由用）。"""

    # ── 健康检查 ──
    def health_snapshot(self) -> dict[str, str]:
        """返回 {server_id: health} 全量快照（healthy / degraded / offline）。"""
    def mark_offline(self, server_id: str, reason: str) -> None:
        """断线 / 超时 → 标记 offline（§2.4），该 server 工具不投影。"""

    # ── 投影合并（capability.py 0 改动）──
    def project_capabilities(self) -> list[CapabilityDefinition]:
        """合并投影：静态 CAPABILITY_DEFINITIONS + 动态注册表（仅 healthy 组）。
        返回的 CapabilityDefinition 与 capability.py 同构（id + expression），
        由 proxy.py 既有投影链注入 prompt（identity 之后、emergent 之前）。"""

    # ── 调用（Actuator 唯一入口，§3）──
    def call(self, tool_id: str, args: dict, *, permission_gate: str) -> ToolResult:
        """单次工具调用。permission_gate: "auto_approved" | "ask_required"（§4.1）。
        硬超时（默认 5s，可配置）→ 超时即放弃 → 降级（§4.2）。"""
```

**接口不变式**：

1. `register_mcp_server` 是**唯一**注册入口；`unregister_mcp_server` 是**唯一**注销入口（无散落 add/remove）。
2. `project_capabilities` 只读、deterministic（按注册顺序 + 静态定义顺序），fail-silent（注册表空 → 只投影静态 3 组，与现状完全等价）。
3. `call` 是 Actuator 调用工具的**唯一**入口——工具调用不绕过 registry 直接触达 MCP client（保证健康检查 + 权限分级 + 降级统一生效）。
4. 注册表不持有 LLM / EventBus / SpeakerToken 引用（与 WorldEventSource ABC 同款隔离，§3.2 硬规则 3）。

### 2.3 自动归类规则（observe / reflect / communicate）

**目标**：MCP 工具按语义自动归入三大能力组，与 `CAPABILITY_DEFINITIONS` 对齐。归类在 `register_mcp_server` 时一次性完成（注册时归类，运行中不重归类）。

**归类规则（优先级从高到低）**：

| 优先级 | 规则 | 说明 |
|---|---|---|
| 1 | **显式映射表**（canonical，TS-2 维护） | 已知工具名 → 组。v1 种子表：`weather / calendar / news / web_search / time / search → observe_environment`；`message_send / telegram_send / dm_send → communicate`；`memory_search / diary_read / memory_retrieve → reflect_memory` |
| 2 | **语义关键词兜底**（description 匹配） | 未命中映射表时，按 description 关键词归类：感知类（weather/calendar/news/search/time/查询/天气/日历/新闻/搜索）→ observe_environment；发送类（send/message/notify/发送/消息/通知）→ communicate；记忆类（memory/diary/recall/记忆/日记/回顾）→ reflect_memory |
| 3 | **无法归类 → 拒绝注册**（fail-closed） | 映射表与关键词均未命中 → 该工具**拒绝注册** + log warning + sidecar 记录。理由：无法归类的工具无法确定其权限语义，不能让它悄悄进入能力组或绕过权限分级 |

**归类不变式**：

1. 每个 RegisteredTool 恰好属于一个能力组（互斥，无多组归属）。
2. 归类结果写入 `RegisteredTool.capability_group`，Actuator 按组路由（§3.1）。
3. 归类失败 ≠ server 注册失败：server 的其余工具照常注册，仅无法归类的单个工具被拒。

**分组聚合原则（TS-0 §2.3 继承，锁死）**：

- Decision 提示词只呈现**能力组**（3 个，与现有 CAPABILITY_DEFINITIONS 对齐），**不呈现工具明细**。
- 工具明细只在执行器层展开（执行时按 Motive 内容路由到具体工具）。
- **Decision 永远只看 3 个能力组，不看 N 个工具**——直接回应「避免 Decision 提示词因工具过多膨胀」。

### 2.4 健康检查与 fail-silent 投影

| 状态 | 判定 | 投影行为 | 调用行为 |
|---|---|---|---|
| `healthy` | MCP server 连接正常（最近一次 ping/调用成功） | 该组工具正常投影 | 正常调用 |
| `degraded` | 最近一次调用超时/异常，但连接未断 | 该组工具正常投影（可观测性：sidecar 记录 degraded） | 调用走降级路径（§4.2） |
| `offline` | 断线 / 连续超时达阈值（默认 2 次，可配置） | **该组工具不投影**（fail-silent）→ 对应 capability 组消失（回退到静态定义或空） | 拒绝调用（返回降级结果，§4.2） |

**健康检查规则**：

1. 健康检查**异步、带超时**（默认 3s），**绝不阻塞主心跳**（§4.3）。
2. 断线/超时 → `mark_offline` → 该 server 工具不投影（fail-silent，prompt 与未接入时等价）。
3. 恢复：下次 `register_mcp_server` 重连成功 → 状态回 `healthy`，工具重新投影。
4. 健康状态变化全部记录 sidecar（append-only）+ logger.info（可观测）。

### 2.5 与 capability.py 的投影合并（capability.py 0 改动）

```
project_capabilities()（tool_registry.py，新增）
  = 静态 CAPABILITY_DEFINITIONS（capability.py，0 改动）
  + 动态注册表 healthy 组的 CapabilityDefinition（按组聚合生成）
  → 合并列表 → proxy.py 既有投影链（identity 之后、emergent 之前注入）
```

- 动态注册表生成的 expression 遵循 CA-3 措辞原则：陈述「可以」（can），不陈述「应」（should）——「你可以感知外部环境（天气、日历、新闻、搜索）」，不写「你应该现在查天气」。
- 合并后仍全量投影（3 组），按组顺序 deterministic；注册表空 → 与现状完全等价（fail-silent）。

---

## 3. 契约 2：Tooling Volition Gate 契约

### 3.1 调用链（Decision 批准 → Actuator 派发单次调用 → 结果回流）

```
外部刺激（MCP server 推送 / 轮询 / 用户消息 / 社交事件）
  │
  ▼
MCP 工具动态发现（tool_registry.py，§2）
  ├─ MCP server 连接 → 工具列表 → 自动归类（§2.3）→ 分组聚合 → CapabilityDefinition
  ├─ server 断线/超时 → 该组工具不投影（fail-silent，§2.4）
  └─ 工具明细只进执行器层，不进 Decision 提示词（§2.3 聚合原则）
  │
  ▼
Motive 评估（MotiveEngine.interpret_new_events）
  └─ 经历 → Soul 的 LLM interpretation → Motive（5 字段冻结）
        └─ 无 motive → 不进 Decision（F1, fail-closed）
  │
  ▼
SM-4 四元单选（decide_motive）── 与 transmit 同权，走同一条 _decision_check 闸门
  ├─ transmit    → Actuator 派发 communicate 组（消息发送，走既有 Expression 路径）
  ├─ observe     → Actuator 派发 observe_environment 组（天气/日历/新闻/搜索）★ 首次获得真实执行路径
  ├─ reflect     → Actuator 派发 reflect_memory 组（记忆检索/日记）★ 首次获得真实执行路径
  └─ do_nothing  → 不执行（合法主动选择）
        │
        ▼
Actuator 派发单次调用（single-shot, 禁递归自激）
  ├─ 工具调用是「一次行动」：执行完即结束，结果只回写感知/认知
  ├─ 严禁：工具结果自动触发新工具调用（MCP server 回调 → 新工具 = 递归自激）
  ├─ 严禁：工具执行绕过 Decision（工具不能 publish AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER）
  └─ 结果处理：observe 结果 → world_context（感知）；reflect 结果 → 记忆摘要（认知）
        │
        ▼
结果回流（§3.3）
  ├─ observe 结果 → WorldPerceptionState / world_context（感知路径，注入 prompt）
  └─ reflect 结果 → 记忆摘要（认知路径，注入 prompt）
```

**Actuator 定位**：执行器层（TS-0 §3.2 的「执行器」正式命名为 **Actuator**）。Actuator 是 Decision 四元与工具执行之间的**唯一派发点**——Decision 只选「组」（observe/reflect/transmit），Actuator 按 Motive 内容路由到组内具体工具（`tool_registry.get_tool` + `call`）。

**Actuator 接口示意（非实现代码）**：

```python
# src/soul/actuator.py（设计示意，TS-2 实现；新增模块，不碰 scheduler 职责）
class Actuator:
    def dispatch(self, decision: DecisionResult, motive: Motive) -> ToolResult | None:
        """按 decision.action 派发单次调用：
        - observe → registry.call(observe_environment 组内路由到的工具, args, permission_gate)
        - reflect → registry.call(reflect_memory 组内路由到的工具, args, permission_gate)
        - transmit → 不在此派发（走既有 Expression 路径，communicate 组是 transmit 的执行器）
        - do_nothing → 返回 None（不执行）
        单次调用，结果不回环（§3.2）。"""
```

### 3.2 0 自主递归硬规则（锁死）

**防「无脑 ReAct 循环工具奴隶」的硬规则**（TS-0 §3.2 继承，TS-2 实现必须逐条落实）：

1. **工具必须是灵魂发自内心想做（Motive）且经 Decision 批准后才发起的单次行动**——工具调用与 transmit 同权，走同一条 `_decision_check` 闸门。scheduler 说查 ≠ Soul 查（TL-2 证明的 Decision 层非装饰语义扩展到工具路径）。
2. **严禁工具内部自主递归自激**：Actuator 是纯函数式单次调用，工具结果**不产生新的 Motive/Decision 循环**；observe 结果只回写感知状态，**不自动触发下一个工具**。MCP server 回调不得进入 Actuator 派发路径。
3. **工具执行器无权 publish AGENT_SPEAK / AGENT_INTENT / AGENCY_TRIGGER**——与 WorldEventSource 同款隔离（`src/world/base.py:13-18` 模式复用）。工具结果只能回流感知/认知（§3.3），不能直接驱动行动。
4. **Decision 提示词不因工具增多而膨胀**：永远只呈现 3 个能力组（§2.3 聚合原则）。
5. **不做无脑 ReAct 循环**：v1 无「工具结果 → 再决策 → 再工具」的多轮链式执行。一次 Decision 批准 = 一次工具调用 = 一次结果回流，结束。

**递归自激的检测与防护**（TS-2 测试锚点）：

| 场景 | 防护 |
|---|---|
| 工具结果包含「建议调用其他工具」 | 结果只回流感知/认知，不进入任何工具路由逻辑（Actuator 无链式入口） |
| MCP server 主动推送事件 | 推送只作为外部刺激进入感知路径（WorldEventSource 模式），不直接触发 Actuator |
| 工具调用超时重试 | 降级（§4.2），**绝不自动重试风暴**（默认 0 自动重试，重试需显式配置且带退避） |

### 3.3 结果回流路径（World Context / Perception）

| 决策 | 结果去向 | 机制 | 语义 |
|---|---|---|---|
| observe | **world_context（感知）** | 结果写入 WorldPerceptionState（ephemeral，24h novelty window 复用）→ 经既有 `AGENT_INTENT_PERCEIVED` / prompt 注入路径进入认知 | 「我感知到了环境」——成为认知背景，不直接触发行动（与感知路径单向数据流一致） |
| reflect | **记忆摘要（认知）** | 结果作为记忆检索摘要注入 prompt（emergent/记忆区块） | 「我回顾了自己的记忆」——认知整理，不产生新行动 |
| transmit | 既有 Expression 路径 | communicate 组是 transmit 的执行器（消息发送），走既有 `_proactive_dm_llm_executor` → `_fire_intent` → LLM → `AGENT_SPEAK` | 与现状一致，0 变更 |

**结果污染防护（TS-0 §4.2 继承）**：

- observe 结果只进 world_context（感知），**不直接写 InnerLifeEvent / SAGE**——除非走既有 WorldInnerLifeAdapter 的 M5.9-2 白名单（`WORLD_QUALIFYING_TYPES`）。防「工具结果 = 经历」的语义污染。
- 工具结果不产生新的 Motive（Motive 只能由 interpretation 产出，SM-1 Q2 冻结）。

---

## 4. 契约 3：权限分级与安全降级

### 4.1 权限分级（Auto-Approved / Ask-Required）

**分级规则**（TS-0 §4.2 继承，锁死）：

| 权限类 | 工具示例 | 守门 | 理由 |
|---|---|---|---|
| **auto_approved**（唯读感知类） | weather / calendar / news / web_search / time / memory_search / diary_read | **Decision 批准即可**，无需 Ask | 只读、无外部副作用、零社交成本；observe/reflect 是内部动作（SM-4.2 内外动作解耦） |
| **ask_required**（敏感变更类） | message_send / telegram_send / 写文件 / 任何外部副作用工具 | **Decision 批准 + Bryan Ask 确认** | 有外部副作用，受社交摩擦力保护；灵魂不能自主发外部消息（TS-0 R6） |

**Ask 守门机制**：

1. 工具注册时按 §2.3 归类 + 权限分级表确定 `permission_class`（显式映射表含权限类；语义兜底默认 `ask_required`——无法确认只读性的工具一律按敏感处理，fail-closed）。
2. `ask_required` 工具：Decision 批准后，Actuator 派发前，**必须**经 Bryan 确认（Ask 弹窗/消息）。v1 先 stub（TS-2 范围：Ask 守门 stub，不接真实确认 UI）。
3. Ask 被拒 / 超时未确认 → 该次调用不执行（fail-closed，等同 do_nothing），结果 = 权限拒绝（`ToolResult.error = "permission_denied"`）。
4. 权限分级与 CA-1 四线正交一致：**Permission 线独立于 Capability 线**——capability 声明「可能」，permission 决定「能不能」，Decision 决定「选不选」。

### 4.2 Fail-closed 平滑降级策略

**外部 MCP Server 断线 / 超时 / 异常 → Fail-closed 平滑降级**（TS-0 §4.2 继承，锁死）：

| 故障 | 降级行为 | 结果 |
|---|---|---|
| 工具调用超时（硬超时 5s，可配置） | 放弃该次调用 → 降级 | 返回空结果或预设缓存（§4.2.1） |
| MCP server 断线 | `mark_offline` → 该组工具不投影（fail-silent） | 对应 capability 组消失；调用返回降级结果 |
| 工具调用异常（非超时） | 捕获异常 → 降级 | 返回空结果或预设缓存 |
| Ask 被拒 / 超时 | 不执行 | 等同 do_nothing（§4.1.3） |

**降级语义（关键区分）**：

- **工具执行失败必须 fail-closed 于「不产生行动」**——失败的工具调用绝不产生 AGENT_SPEAK / AGENT_INTENT / 任何外部副作用。
- **但允许「感知缺失」静默**——observe 失败 → 不注入 world_context，等同没感知；感知失败不阻断生命（与感知层 fail-open 的「不 crash」精神一致，但工具层更严格：不产生行动）。

**4.2.1 降级结果形态（空结果 / 预设缓存）**：

| 形态 | 适用 | 说明 |
|---|---|---|
| **空结果** | 无缓存可用 | `ToolResult(ok=False, data=None, degraded=True)`；observe → 不注入 world_context；reflect → 不注入记忆摘要 |
| **预设缓存** | 有最近一次成功结果 | 返回最近一次成功结果 + `cached=True` + staleness 标注（如「3 小时前的天气」）；调用方（Actuator）决定是否注入（带陈旧标记注入，避免把旧数据当新感知） |

**降级不变式**：

1. 降级路径**绝不 crash、绝不自动重试风暴**（默认 0 自动重试；重试需显式配置且带指数退避）。
2. 降级结果**绝不阻塞主心跳**（§4.3）。
3. 降级事件全部记录 sidecar（append-only）+ logger.warning（可观测，不静默吞错）。

### 4.3 主心跳不阻塞保证（锁死）

**绝不阻塞主心跳**——工具层任何故障不得拖垮 Soul 的主循环（scheduler / heartbeat / Agency）：

1. **硬超时**：所有工具调用带硬超时（默认 5s，可配置），超时即放弃 → 降级（§4.2）。与 Fish TTS 的 180s timeout 对比：工具调用是感知/认知路径，超时必须远小于主心跳周期。
2. **异步健康检查**：健康检查异步执行（`create_managed_task` 模式，与 polling loop 同款），带超时（默认 3s），不阻塞主线程。
3. **隔离执行**：工具调用在独立 task 中执行（`asyncio.to_thread` / `create_task` 模式，参照 FishTTSHandler `_synthesize_async` 先例），主循环不 await 工具结果。
4. **降级兜底**：任何未捕获异常 → 降级路径（§4.2），主循环继续。

---

## 5. 契约 4：冻结契约审查（0 改动确认）

### 5.1 逐条审查表

| Frozen Contract | 位置 | 本设计是否触碰 | TS-1/TS-2 约束 |
|---|---|---|---|
| **Agency 4 stages** | `src/agency/stages.py` | ❌ 未触碰 | 不得改；工具执行器在 Actuator 层（M5.11-2 先例），不在 Stage 4 |
| **TriggerEnvelope** | `src/agency/trigger.py` | ❌ 未触碰 | 不得改；工具调用不新增 trigger_type |
| **InnerLifeEvent** | `src/inner_life/event.py` | ❌ 未触碰 | 不得改；工具结果不直接写 InnerLifeEvent（§3.3 污染防护） |
| **4 handlers** | `src/agency/*_handler.py` | ❌ 未触碰 | 不得改 |
| **SAGE 写入逻辑** | `src/memory/sage/` | ❌ 未触碰 | 不得改；工具结果走既有 adapter 白名单（M5.9-2）或只进 world_context |
| **EventBus** | `src/eventbus/`（schema / bus） | ❌ 未触碰 | 不得改；工具执行器不新增 EventType、不 publish（§3.2 硬规则 3）；工具结果回流走既有感知路径（§3.3） |
| **SM-2 Decision Prompt 四块** | `src/soul/decision.py` | ❌ 未触碰 | 工具能力组只进 Relevant context（social_context 先例，SI-3 Phase 2）；Framing/Boundary 不动 |
| **Motive 5 字段** | `src/soul/motive.py` | ❌ 未触碰 | 不得改；工具执行不新增 Motive 字段 |
| **CA-1 四线正交** | `src/soul/capability.py` | ❌ 未触碰 | 动态注册表独立于 capability.py（§2.1）；Permission 线独立（§4.1.4） |
| **DSH ROLE_CAPABILITIES 隔离** | `src/work/roles.py` | ❌ 未触碰 | 不共用命名空间、不互相引用（CA-1 Q7） |
| **M3.1 WorldEventSource ABC** | `src/world/base.py` | ❌ 未触碰 | 工具执行器复用同款隔离模式（无权 publish AGENT_SPEAK，§3.2 硬规则 3） |
| **scheduler 职责** | `src/soul/scheduler.py` | ❌ 未触碰 | 只加 additive producer-side 检查点（M5.8-4 / SM-3 同款先例）；wake/opportunity 职责不变 |

### 5.2 结论

**0 CONTRACT CONFLICT。** 本设计为 docs-only，未创建/修改任何 source/test/config/data 文件（唯一产出物为本文档）；TS-2 实作必须恪守上表约束——SAGE / EventBus / Agency 4-stage 零代码修改。

---

## 6. 验收对照

| 验收项 | 结果 |
|---|---|
| 设计文档产出（docs/TOOLING-MCP-CONTRACT.md） | ✅ 本文档 |
| 覆盖 tool_registry 接口（动态注册 / 健康检查 / 自动归类至 observe/reflect/communicate） | ✅ §2 |
| 覆盖 Tooling Volition Gate 契约（Decision 批准 → Actuator 派发单次调用 → 结果回流 World Context / Perception，0 自主递归） | ✅ §3 |
| 覆盖权限分级 + 安全降级（唯读感知 Auto-Approved / 敏感变更 Ask-Required / 断线超时 Fail-closed 降级至空结果或预设缓存 / 不阻塞主心跳） | ✅ §4 |
| 覆盖冻结契约审查（SAGE / EventBus / Agency 4-stage 零代码修改） | ✅ §5 |
| 明确「只设计，0 code」 | ✅ §0 / §7 |
| 不碰 frozen contract | ✅ §5（0 CONTRACT CONFLICT） |
| 不 commit、不 push（等验收） | ✅ 唯一产出物为本文档 |

---

## 7. 边界与不做（Out of Scope）

- ❌ 实作 tool_registry.py / Actuator / Volition Gate 接线（TS-2 才做）
- ❌ 接任何真实 MCP server（TS-3 才做）
- ❌ 改 capability.py / decision.py / motive.py / scheduler.py / proxy.py
- ❌ 改 Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE / EventBus
- ❌ 建 scoring / Qualification / 工具价值公式
- ❌ 工具结果写 InnerLifeEvent / SAGE（除非走既有 M5.9-2 白名单 adapter）
- ❌ 改任何 code、不 commit、不 push

---

## 8. 下一步（供主大脑参考，非本工单范围）

- **TS-2（IMPLEMENTATION）**：落地 `src/soul/tool_registry.py`（§2 接口）+ `src/soul/actuator.py`（§3 派发）+ observe/reflect 执行器（先接现有 Open-Meteo/日历/RSS 的查询接口，不接新 server）+ `scheduler._decision_check` additive 扩展（observe/reflect 选择后调用 Actuator，单次行动，结果只回写感知/认知，不 publish）+ 权限分级（Ask 守门 stub）+ 测试（tool_registry 单测 + volition gate 集成测试，TL-2 模式复用）。
- **TS-3（后续，CANDIDATE）**：接第一个真实 MCP server，验证动态发现 + 断线降级 + Ask 守门端到端。需 Owner 授权（per ENGINEERING_STATE §5 transition rule）。
