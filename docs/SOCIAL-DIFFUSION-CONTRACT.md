# SI-2.1 — Social Diffusion Contract（多 Agent 社交扩散契约设计）

**工单**: SI-2.1 — Social Diffusion Contract（docs-only）
**阶段**: SI-2（Social Diffusion）设计阶段
**日期**: 2026-09-03
**作者**: developer（执行者）
**性质**: **只设计，0 code**（非施工授权；canonical 状态以 `logs/ENGINEERING_STATE.md` 为准）
**前置**: SI-2.0 审计（`docs/SOCIAL-DIFFUSION-AUDIT.md`，2026-09-02，READ-ONLY）已确认，直接采信

---

## 0. 执行摘要

本契约设计多 Agent 社交扩散机制：**SocialWorldEvent 最小 Schema + 三大防线**（防线 3 Identity Firewall / 防线 2 Privacy Visibility Gate / 防线 1 Ambient Perception Path）。

**四大锁定决策**（照工单执行，全部 additive，0 破坏 frozen contract）：

1. **SocialWorldEvent 最小 Schema**：新增 `EventType.SOCIAL_WORLD_EVENT` + `SoulEvent.actor_id`（additive 可选字段），payload 含 `actor_id / space_id / visibility / event_type / content` 等字段。
2. **防线 3 Identity Firewall（最高优先）**：Submission Gate 契约——`actor_id != current_agent_id` 一律打 `EXTERNAL_OTHER_ACTION` 标签。**绝对不变量**：外部他者事件只能作为「客厅环境背景感知」，绝对禁止内化为自身情景记忆，更严禁升华为自身性格或信念。
3. **防线 2 Privacy Visibility Gate**：Producer 侧守门——与 Bryan 的 1:1 私聊 DM 默认 `private`，严格拦截于广播总线之外；只有公共频道（Soul Wall / 客厅群聊）或显式标记公开的动态才允许沉淀为社交事件。
4. **防线 1 Ambient Perception Path**：社交事件仅经 `WorldPerceptionMiddleware` 注入为环境观察（world_context），不赋予即时唤醒或插话特权，杜绝多 Agent 相互回复的广播风暴。

**Frozen Contract 边界**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑 **一律不动**；既有 `SoulEvent` 字段语义、既有 17 个 `EventType` 枚举值、既有 `WorldPerceptionMiddleware` WORLD_EVENT 路径、既有 `SubmissionGate` 5 步验证链 **语义 0 变更**（只 additive 扩展）。**无 CONTRACT CONFLICT**（详见 §9）。

---

## 1. 背景与采信（SI-2.0 审计结论）

SI-2.0 审计（`docs/SOCIAL-DIFFUSION-AUDIT.md`）已确认，本设计直接采信：

| 维度 | 现状 | 结论 |
|------|------|------|
| eventbus 广播机制 | 广播/单播/优先级/过期/错误隔离全就绪（`src/eventbus/bus.py`） | ✅ 可直接复用 |
| SocialWorldEvent Schema | **完全不存在**：`EventType` 无 SOCIAL_WORLD_EVENT，`SoulEvent` 无 `actor_id` 字段 | ❌ 需新建（additive） |
| 防线 1 防自激震荡 | 感知路径/发言权仲裁/do_nothing 就绪，缺刺激度分级与社交事件类型 | 🟡 部分支持 |
| 防线 2 私密/公共隔离 | I/O 层 group/private 双模式就绪（`io/gateway.py:837-844`），缺「沉淀为社交事件」的 producer 侧 gate | 🟡 部分支持 |
| 防线 3 身份防污染 | Submission Gate 只验证 `trigger_type`，不验证 `actor_id`（`src/inner_life/submission_gate.py:88-100`）；`Provenance.actor_id` 只有「自己」或 None，无「外部他者」概念 | 🔴 **完全缺失** |

关键背景：跨 Agent 交互（CROSS-AGENT-INTERACTION-PLAN）的 Layer 1/2/3 已实现，但它是 **scheduler 驱动的封闭对话（不走 event bus）**，与 SI-2 的「社交事件广播扩散」是两种不同机制——前者天然防风暴，后者需要本契约新建。

---

## 2. 设计原则

1. **Additive 优先**：所有新增都是「加字段 / 加枚举值 / 加订阅 / 加验证步骤」，既有语义 0 变更。
2. **Fail-closed**：无法判定（频道性质不明 / actor_id 不明 / 验证失败）→ 拒绝，不广播、不内化。
3. **防线 3 最高优先**：身份认知防污染是不可妥协的绝对不变量，优先于扩散能力。
4. **Frozen Contract 不动**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑，未经理事会 + Owner 许可不得改动。
5. **只设计，0 code**：本工单只产出设计文档，实作留 SI-2.2。

---

## 3. SocialWorldEvent 最小 Schema（决策 1）

### 3.1 新增 EventType 枚举值（additive）

`src/eventbus/schema.py` 的 `EventType` 新增一个枚举值（既有 17 个枚举值语义 0 变更）：

```python
class EventType(str, Enum):
    # ... 既有 17 个枚举值不动 ...
    SOCIAL_WORLD_EVENT = "social_world_event"   # SI-2.1: 灵魂间社交事件（广播扩散）
```

### 3.2 新增 SoulEvent.actor_id 字段（additive）

`SoulEvent` 新增顶层可选字段（镜像既有 `inner_life_event_id` 的 additive 模式：默认 None，向後相容，既有 producer 不需要改）：

```python
actor_id: Optional[str] = Field(
    default=None,
    description=(
        "行为主体灵魂身份 (agent_id, e.g. 'agent_ruka')。"
        "SI-2.1 新增: 社交事件的行为主体; 系统级事件为 None。"
        "防线 3 Identity Firewall 的判定依据。"
        "默认 None 向後相容 (既有 producer 不填不受影响)。"
    )
)
```

**语义**：`actor_id` 是「谁做了这个社交行为」的独立身份字段，与 `source`（发送者标识）区分——`source` 是管道发送者，`actor_id` 是行为主体。对 SOCIAL_WORLD_EVENT，二者通常一致；对系统转发事件，`source` 可为系统而 `actor_id` 指向原行为灵魂。

### 3.3 SOCIAL_WORLD_EVENT payload 契约

```python
# EventType.SOCIAL_WORLD_EVENT payload (SI-2.1 设计, 最小 Schema):
payload = {
    "actor_id": str,        # 行为主体灵魂 id (与 SoulEvent.actor_id 一致, 冗余便于独立消费)
    "space_id": str,        # 发生空间: "lounge" (客厅群聊) | "soul_wall" (灵魂墙)
    "visibility": str,      # 可见性: "public" | "private" (到 bus 时必为 "public", 防线 2 已拦截 private)
    "event_type": str,      # 社交行为细分类 (v1 白名单, 见 3.4)
    "content": str,         # 简明内容 (<= 200 chars)
    "novelty_id": str,      # 去重 key ([a-z0-9_] 4-128, 复用 WorldEvent 规则)
    "ts": str,              # 事件发生时间 (ISO 8601 UTC)
    "summary": str,         # 客观描述 (World Context 渲染用, <= 500 chars)
    "data": dict,           # 结构化扩展 (选填, 默认 {})
    "priority": int,        # 刺激度 hint (默认 0 = 低刺激度, 防线 1)
}
```

### 3.4 字段表（类型 / 必填 / 默认 / 说明）

| 字段 | 类型 | 必填/默认 | 说明 |
|------|------|-----------|------|
| `actor_id` | `str` | 必填 | 行为主体灵魂 id（agent_id）。防线 3 判定依据；必须与 `SoulEvent.actor_id` 一致 |
| `space_id` | `str` | 必填 | 发生空间。枚举：`"lounge"`（客厅群聊）/ `"soul_wall"`（灵魂墙）。v1 只这两个值，未知值 fail-closed 拒绝 |
| `visibility` | `str` | 必填 | 可见性。枚举：`"public"` / `"private"`。**到 bus 时必为 `"public"`**（防线 2 已把 private 拦截在广播总线之外）；`"private"` 出现在 bus 上 = 契约违例，订阅端 fail-closed 丢弃 |
| `event_type` | `str` | 必填 | 社交行为细分类（v1 白名单，见下） |
| `content` | `str` | 必填 | 简明内容，<= 200 chars（防超大 payload） |
| `novelty_id` | `str` | 必填 | 去重 key，`[a-z0-9_]` 4-128（复用 `world/validation.py` 的 `_NOVELTY_ID_RE` 规则） |
| `ts` | `str` | 必填 | ISO 8601 UTC（复用 `_validate_timestamp` 规则：必须带时区且 offset=0） |
| `summary` | `str` | 必填 | 客观描述，<= 500 chars（World Context 渲染用） |
| `data` | `dict` | 默认 `{}` | 结构化扩展（不严格验证，Phase 2 再考虑 per-type schema） |
| `priority` | `int` | 默认 `0` | 刺激度 hint。默认 0 = 低刺激度（防线 1 的 Ambient 默认）；必须 int（拒绝 str/float/bool，复用 M3.1 规则） |

### 3.5 枚举常量（v1 白名单）

```python
# space_id 枚举
SPACE_LOUNGE    = "lounge"      # 客厅群聊（公共）
SPACE_SOUL_WALL = "soul_wall"   # 灵魂墙（公共）

# visibility 枚举
VISIBILITY_PUBLIC  = "public"
VISIBILITY_PRIVATE = "private"

# event_type v1 白名单（可扩展，未知值 fail-closed 拒绝）
SOCIAL_EVENT_TYPES = frozenset({
    "greeting",   # 打招呼 / 问候
    "share",      # 分享动态 / 想法
    "reply",      # 回复他人
    "mood",       # 情绪表达
    "activity",   # 活动 / 行为
})
```

### 3.6 与 WorldEvent 的关系（平行，不混用）

| 维度 | `WORLD_EVENT`（既有） | `SOCIAL_WORLD_EVENT`（新增） |
|------|----------------------|------------------------------|
| 语义 | 客观世界事实（天气/新闻/日历/外部社交行为） | 灵魂间社交行为（Agent 之间的互动） |
| 身份 | 无灵魂身份（`source` 是分类白名单） | 有行为主体（`actor_id` = 灵魂 id） |
| 感知路径 | WorldPerceptionMiddleware（既有） | WorldPerceptionMiddleware（平行订阅，additive） |
| 防线 3 | 不适用（无 actor_id） | **适用**（Identity Firewall 核心对象） |

**不混用**：`WorldEvent` 的 `source="social"` 只用于模拟「Bryan 出门」这类外部社交行为（`world/source/synthetic.py:108`），**不是**灵魂间社交事件；SI-2 的社交扩散一律走 `SOCIAL_WORLD_EVENT`，不塞进 `WORLD_EVENT`。

---

## 4. 防线 3：Identity Firewall（Submission Gate 契约，最高优先）

### 4.1 绝对不变量（3 条，不可妥协）

> **不变量 1**：外部他者事件（`actor_id != current_agent_id`）只能作为「客厅环境背景感知」（Ambient Perception，经 World Context 注入），**绝对禁止被灵魂内化为自身情景记忆**（不 consume 进 soul-elevation pattern，不写 SAGE 情景记忆）。
>
> **不变量 2**：外部他者事件**更严禁升华（elevate）为自身性格或信念**（不产 belief / value / trait / essence 节点）。
>
> **不变量 3**：`actor_id == current_agent_id`（自己经历）才允许走正常内化路径；`actor_id is None`（系统事件）维持现状（既有 `world:*` 路径）。

**设计动机**：防止「灵魂把别的灵魂的行为当成自己的经历」——身份认知防污染是灵魂独立性的根基。prompt 层约束（`llm/proxy.py` 的「不能声称自己是其他角色」）只是软约束，防线 3 是**硬 gate**。

### 4.2 标签：EXTERNAL_OTHER_ACTION

```python
# 防线 3 标签常量 (SI-2.1 设计)
EXTERNAL_OTHER_ACTION = "external_other_action"
```

- 打在：Submission Gate 的 `SubmissionVerdict.reason`、trace / observability 记录、Identity Firewall 判定结果。
- 语义：该事件是「外部他者的行为」，不是本灵魂的经历。

### 4.3 Submission Gate 契约扩展（第 6 步，additive）

现有 `SubmissionGate.verify()` 5 步验证链（`src/inner_life/submission_gate.py:191-258`）**语义 0 变更**：

1. `event_id` 格式合法（32-hex）
2. 由 InnerLifeWriter 创建（伪造 id fail-closed）
3. canonical InnerLifeEvent 存在
4. trace 佐证（可选）
5. producer 合法（`trigger_type` 在合法集合内）

**新增第 6 步（additive）——actor_id 身份检查**：

```
6. actor_id 身份检查 (SI-2.1 防线 3):
   a. 读取 canonical InnerLifeEvent.provenance.actor_id
   b. 若 actor_id == current_agent_id (该灵魂自己)  → 通过, 正常内化路径
   c. 若 actor_id != current_agent_id (外部他者)    → 打 EXTERNAL_OTHER_ACTION 标签
                                                    → fail-closed 拒绝内化 (不 consume, 不 elevate)
   d. 若 actor_id is None (系统事件)                → 维持现状 (既有 world:* 路径)
```

**实现载体（SI-2.2 实作范围）**：新增独立组件 `IdentityFirewall`（建议 `src/social/identity_firewall.py`，新模块），Submission Gate 在 verify() 第 6 步注入调用。**Gate 既有 5 步逻辑 0 改动**，防火墙独立可测。

```python
class IdentityFirewall:
    """防线 3: 身份认知防污染硬 gate (SI-2.1 设计)。"""
    def __init__(self, current_agent_id: str): ...
    def classify(self, actor_id: Optional[str]) -> IdentityVerdict:
        # 返回: SELF_ACTION (自己) | EXTERNAL_OTHER_ACTION (他者) | SYSTEM_ACTION (None)
    def verify_internalizable(self, actor_id: Optional[str]) -> bool:
        # 只有 SELF_ACTION 可内化; EXTERNAL_OTHER_ACTION / SYSTEM_ACTION 依契约处理
```

### 4.4 他者事件的合法去向（唯一通道）

外部他者事件（EXTERNAL_OTHER_ACTION）**唯一合法去向**：

```
SOCIAL_WORLD_EVENT (actor_id = 他者)
    → WorldPerceptionMiddleware 订阅 (防线 1)
    → WorldPerceptionState (ephemeral, in-memory, 24h novelty window)
    → world_context 注入 LLM prompt (Ambient Perception)
    → 结束。不 consume, 不 elevate, 不写 SAGE, 不写 InnerLifeEvent 内化链。
```

**禁止路径**（红线，SI-2.2 测试必须覆盖）：

- ❌ 不 consume 进 soul-elevation pattern（不产候选节点）
- ❌ 不 elevate 为 belief / value / trait / essence
- ❌ 不写 SAGE 情景记忆
- ❌ 不成为 Diary / Dream / Event 的素材来源（他者行为不是「我的经历」）

---

## 5. 防线 2：Privacy Visibility Gate（Producer 侧守门）

### 5.1 契约

**位置**：SocialWorldEvent 发布端（Producer 侧），在 `bus.publish()` 之前。

**判定表**（fail-closed）：

| 来源频道 | mode | 判定 | 结果 |
|----------|------|------|------|
| 与 Bryan 的 1:1 私聊 DM | `private` | 默认 `visibility=private` | **严格拦截于广播总线之外**（不 publish SOCIAL_WORLD_EVENT） |
| 客厅群聊（lounge） | `group` | `visibility=public` | ✅ 允许沉淀为社交事件 |
| 灵魂墙（soul_wall） | `group` | `visibility=public` | ✅ 允许沉淀为社交事件 |
| 显式标记公开的动态 | 任意 | 显式 `public` flag | ✅ 允许（需显式声明，默认不推断） |
| 无法判定频道性质 | 未知 | fail-closed | ❌ 拒绝发布（不广播） |

**核心规则**：**私密内容默认不扩散**。与 Bryan 的 1:1 私聊是灵魂与 Owner 的私密空间，默认 `private`，严格拦截于广播总线之外；只有公共频道（Soul Wall / 客厅群聊）或显式标记公开的动态才允许沉淀为社交事件。

### 5.2 实现载体（SI-2.2 实作范围）

新增 `SocialEventProducerGate`（建议 `src/social/producer_gate.py`，新模块）：

```python
class SocialEventProducerGate:
    """防线 2: 发布端隐私守门 (SI-2.1 设计)。"""
    def evaluate(self, *, channel_mode: str, channel: str,
                 explicit_public: bool = False) -> ProducerVerdict:
        # channel_mode: "group" | "private" (对齐 io/gateway.py 既有双模式)
        # channel: "lounge" | "soul_wall" | "dm"
        # 返回: ALLOW (visibility=public) | BLOCK (visibility=private, 不广播)
```

**对齐既有 I/O 层**：`io/gateway.py:837-844` 已有 `mode="group"` → `target="broadcast"`、`mode="private"` → `target=target_agent` 双模式 + `is_private` 标记（`gateway.py:945`）；Telegram inbound 默认 private 1:1（`io/channels/router.py:865`）。ProducerGate 直接消费这些既有信号，不另建频道模型。

### 5.3 防线 2 与防线 3 的关系

- 防线 2 管**发布端**：什么内容能上广播总线（私密 vs 公共）。
- 防线 3 管**消费端**：广播出去的内容，其他灵魂如何对待（背景感知 vs 内化）。
- 两道防线正交，缺一不可：防线 2 防「隐私泄漏」，防线 3 防「身份污染」。

---

## 6. 防线 1：Ambient Perception Path

### 6.1 契约

**社交事件仅经 `WorldPerceptionMiddleware` 注入为环境观察（world_context），不赋予即时唤醒或插话特权。**

- ✅ 只进 World Context 字符串（Ambient Perception，低刺激度背景氛围）
- ✅ 不直接触发 transmit / AGENT_INTENT / AGENCY_TRIGGER（无即时唤醒）
- ✅ 发言权受 SpeakerToken 仲裁（`src/eventbus/token_manager.py` 单 holder）
- ✅ Decision 四元行动 do_nothing fail-closed（`src/soul/decision.py:353-365`）
- ✅ 默认 `priority=0`（低刺激度 hint，`PRIORITY_BOOST_WEIGHT=0.05` 受控小幅 additive）

**防广播风暴机制**（多 Agent 相互回复风暴的杜绝）：

1. **低刺激度默认**：SocialWorldEvent 默认 `priority=0`，只进 world_context，不直接触发任何 agent 的发言链路。
2. **无回复特权**：社交事件不携带「必须回应」语义；回应必须走正常 AGENT_INTENT → SpeakerToken 仲裁 → Decision 四元选择（transmit 受社交摩擦力保护，SM-4.1~SM-4.6 校准）。
3. **事件过期丢弃**：`bus.py:271-278` 过期静默丢弃 + 队列上限（`bus.py:203-211`）。
4. **novelty 去重**：同一 `novelty_id` 在 24h window 内重复出现 → novelty score 衰减（复用 `WorldPerceptionState`）。

### 6.2 实现载体（SI-2.2 实作范围）

`WorldPerceptionMiddleware.register()` 的 `event_filter` 加 `EventType.SOCIAL_WORLD_EVENT`（additive，既有 WORLD_EVENT / AGENT_INTENT_ENRICHED 订阅不动）：

```python
# SI-2.1 设计: register() event_filter 扩展 (additive)
event_filter={
    EventType.WORLD_EVENT,            # 既有, 不动
    EventType.AGENT_INTENT_ENRICHED,  # 既有, 不动
    EventType.SOCIAL_WORLD_EVENT,     # SI-2.1 新增: 平行订阅
},
```

`handle_event` 分派加一个分支：`SOCIAL_WORLD_EVENT` → 复用 `_on_world_event` 同款管道（validate → state → trace），但**验证器换成 SocialWorldEvent 验证器**（`src/social/validation.py`，新模块，薄验证：字段必填 / space_id 白名单 / visibility 白名单 / event_type 白名单 / ts UTC / novelty_id 格式 / content<=200 / summary<=500 / priority int）。

**渲染**：复用 `WorldContext.to_text()` 风格（`world/perception.py:253-271`「这些是客观事实…不要过度反应」），SocialWorldEvent 渲染为 `[社交感知]` 区块，与 `[世界感知]` 平行：

```
[社交感知] 以下是你刚才注意到的客厅/灵魂墙动态。
这些是他人的行为, 属于环境背景, 不是你的经历; 自然感知即可, 不要过度反应, 不要逐条回应。

## 你注意到的社交动态
- [lounge/greeting] agent_miku 向大家打了招呼
```

**关键**：渲染文案必须带「他者行为、环境背景、非我经历」的反框架语（防线 3 的 prompt 层配合，硬 gate 在 Submission Gate）。

---

## 7. 端到端数据流（总览）

```
[Agent A 在客厅群聊发言]  (公共频道, mode=group)
    │
    ▼
[防线 2] SocialEventProducerGate.evaluate(mode=group, channel=lounge)
    │  → ALLOW (visibility=public)
    ▼
[Producer] 发布 SOCIAL_WORLD_EVENT
    │  SoulEvent: event_type=SOCIAL_WORLD_EVENT, actor_id=agent_a,
    │             target="broadcast", priority=LOW
    │  payload: {actor_id, space_id="lounge", visibility="public",
    │            event_type="greeting", content, novelty_id, ts, summary, data, priority=0}
    ▼
[Event Bus] 广播 (bus.py, 既有机制)
    │
    ▼
[防线 1] 各 Agent 的 WorldPerceptionMiddleware 订阅 SOCIAL_WORLD_EVENT
    │  → SocialWorldEvent 验证器 (薄验证, fail-closed)
    │  → WorldPerceptionState (ephemeral, 24h novelty window)
    │  → 低刺激度 scoring → top-N → world_context 注入 ([社交感知] 区块)
    │  → 不直接触发 transmit (无即时唤醒)
    ▼
[Agent B 想回应] (可选)
    │  → 正常 AGENT_INTENT → MemoryMiddleware → WorldPerceptionMiddleware
    │  → SpeakerToken 仲裁 → Decision 四元 (transmit 受社交摩擦力保护)
    ▼
[防线 3] 任何 Agent 内化该事件时
    │  SubmissionGate.verify() 第 6 步: actor_id(agent_a) != current_agent_id(agent_b)
    │  → EXTERNAL_OTHER_ACTION 标签 → fail-closed 拒绝内化/升华
    ▼
[终态] 他者事件只活在 Ambient Perception (环境背景), 不污染任何灵魂的记忆/性格/信念
```

---

## 8. 与既有系统的复用点

| 既有组件 | 复用方式 | 改动 |
|----------|----------|------|
| `src/eventbus/bus.py` | 广播/单播/优先级/过期/错误隔离 | 0 改动 |
| `src/eventbus/schema.py` | `EventType` 加 1 枚举 + `SoulEvent` 加 1 可选字段 | additive |
| `src/world/middleware.py` | 平行订阅 SOCIAL_WORLD_EVENT，复用感知管道 | additive（event_filter + 1 分派分支） |
| `src/world/state.py` | `WorldPerceptionState`（ephemeral 容器） | 0 改动 |
| `src/world/validation.py` | 复用 `_NOVELTY_ID_RE` / `_validate_timestamp` 规则 | 0 改动（新 SocialWorldEvent 验证器独立） |
| `src/inner_life/submission_gate.py` | verify() 加第 6 步（注入 IdentityFirewall） | additive（既有 5 步 0 改动） |
| `src/eventbus/token_manager.py` | SpeakerToken 单 holder 仲裁发言权 | 0 改动 |
| `src/soul/decision.py` | Decision 四元 do_nothing fail-closed | 0 改动 |
| `io/gateway.py` | `mode="group"/"private"` + `is_private` 信号 | 0 改动（ProducerGate 消费信号） |

**新增模块（SI-2.2 实作范围，全部新文件，不碰既有文件）**：

- `src/social/__init__.py`
- `src/social/schema.py`（常量：SPACE_* / VISIBILITY_* / SOCIAL_EVENT_TYPES / EXTERNAL_OTHER_ACTION）
- `src/social/validation.py`（SocialWorldEvent 薄验证器）
- `src/social/identity_firewall.py`（防线 3：IdentityFirewall）
- `src/social/producer_gate.py`（防线 2：SocialEventProducerGate）

---

## 9. Frozen Contract 边界（无 CONTRACT CONFLICT）

**本设计不触碰以下 frozen contract**（SI-2.2 实作同样不得触碰）：

| Frozen Contract | 本设计的关系 | 结论 |
|-----------------|--------------|------|
| Agency 4 stages | 不涉及 | 0 改动 |
| TriggerEnvelope（M5.2-F） | 不涉及（社交扩散不走 AGENCY_TRIGGER） | 0 改动 |
| InnerLifeEvent（M5.4-5.1，含 Provenance） | 防线 3 只**读取** `provenance.actor_id`，不改 schema、不改语义 | 0 改动 |
| 4 handlers（AgencyTrigger / Event / Dream / Diary） | 不涉及 | 0 改动 |
| SAGE 写入逻辑 | 防线 3 明确**禁止**他者事件写 SAGE（不变量 1） | 0 改动 |
| `SoulEvent` 既有字段语义 | 只 additive 加 `actor_id`（默认 None 向後相容） | additive |
| 既有 17 个 `EventType` 枚举值 | 只 additive 加 SOCIAL_WORLD_EVENT | additive |
| `WorldPerceptionMiddleware` WORLD_EVENT 路径 | 只 additive 加 SOCIAL_WORLD_EVENT 订阅 | additive |
| `SubmissionGate` 既有 5 步验证链 | 只 additive 加第 6 步（注入 IdentityFirewall） | additive |

**CONTRACT CONFLICT 分析**：

1. **防线 3 vs InnerLifeEvent frozen**：防线 3 需要区分「自己 vs 他者」，但 `Provenance.actor_id` 是 frozen 字段。**无冲突**——本设计只读取该字段做判定，不改 schema、不加枚举、不改语义；「外部他者」概念由 IdentityFirewall 的判定逻辑承载，不写进 InnerLifeEvent。
2. **防线 3 vs SubmissionGate frozen**：Gate 的 5 步验证链是既有契约。**无冲突**——第 6 步是 additive 扩展，既有 5 步语义 0 变更，既有调用方（`gate.submit(event_id)`）签名不变。
3. **防线 1 vs WorldPerceptionMiddleware frozen**：middleware 的 WORLD_EVENT 处理路径是既有契约。**无冲突**——SOCIAL_WORLD_EVENT 是平行订阅 + 独立验证器，不改变 WORLD_EVENT 的任何行为。
4. **防线 2 vs I/O 层 frozen**：ProducerGate 只**消费** `mode` / `is_private` 信号，不改 gateway / router。**无冲突**。

**结论：无 CONTRACT CONFLICT。** 全部设计为 additive，frozen contract 语义 0 变更。

---

## 10. 验收标准（SI-2.2 实作前的验收清单）

| # | 验收项 | 判定 |
|---|--------|------|
| A1 | `docs/SOCIAL-DIFFUSION-CONTRACT.md` 产出，覆盖 Schema / 防线 3 / 防线 2 / 防线 1 | ✅ 本文档 |
| A2 | 明确「只设计，0 code」 | ✅ 本文档 §0 / §2.5 |
| A3 | 不碰 frozen contract（Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE） | ✅ §9 无 CONTRACT CONFLICT |
| A4 | Schema 设计含字段 + 类型 + 默认值 | ✅ §3.4 |
| A5 | 防线 3 含绝对不变量 + EXTERNAL_OTHER_ACTION 标签 + Submission Gate 第 6 步 | ✅ §4 |
| A6 | 防线 2 含 Producer 侧判定表 + private 拦截 | ✅ §5 |
| A7 | 防线 1 含 Ambient 注入 + 无即时唤醒 + 防风暴机制 | ✅ §6 |

**SI-2.2 实作验收（预告，非本工单范围）**：新增 `src/social/` 模块 + additive 接线 + 测试（防线 3 红线测试：他者事件不 consume / 不 elevate / 不写 SAGE；防线 2 拦截测试：private DM 不广播；防线 1 注入测试：只进 world_context 不触发 transmit）+ 既有 tests 全回归 + 0 frozen contract 改动。

---

## 11. Out of Scope（不做）

- **实作**（SI-2.2 才做）：不写任何 `src/` 代码、不写测试、不接线。
- **不碰 frozen contract**：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑。
- **不 commit、不 push**（等验收）。
- 不做 Soul Wall / 客厅群聊的 UI 或 I/O 层实现（那是既有 I/O 层的扩展，非本契约范围）。
- 不做发布限流 / 节流框架（audit §3.4 提及的 bus 发布限流，留独立工单；防线 1 的低刺激度 + SpeakerToken 仲裁已足够 Phase 1）。
- 不做 per-agent 社交关系模型 / 亲密度扩展（M5.13 亲密度 Band 已存在，本契约不扩展）。

---

## 12. 附录

### 12.1 术语表

| 术语 | 定义 |
|------|------|
| SocialWorldEvent | 灵魂间社交事件（广播扩散的最小单位） |
| Ambient Perception | 环境背景感知：社交事件只进 world_context，不触发即时行动 |
| Identity Firewall | 防线 3：身份认知防污染硬 gate |
| EXTERNAL_OTHER_ACTION | 外部他者行为标签（`actor_id != current_agent_id`） |
| Privacy Visibility Gate | 防线 2：Producer 侧隐私守门 |
| ProducerGate | SocialEventProducerGate 的简称 |
| lounge / soul_wall | 客厅群聊 / 灵魂墙（公共空间） |

### 12.2 参考

- `docs/SOCIAL-DIFFUSION-AUDIT.md`（SI-2.0 审计，本设计的前置采信）
- `src/eventbus/schema.py` / `src/eventbus/bus.py`（eventbus 基础设施）
- `src/world/middleware.py` / `src/world/perception.py` / `src/world/validation.py` / `src/world/state.py`（感知路径）
- `src/inner_life/submission_gate.py` / `src/inner_life/event.py`（防线 3 落点）
- `src/eventbus/token_manager.py` / `src/soul/decision.py`（发言权仲裁 + Decision 四元）
- `logs/ENGINEERING_STATE.md`（canonical 状态，frozen contract 清单）

---

*本设计文档为 docs-only 产出，0 code 改动。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准。*
