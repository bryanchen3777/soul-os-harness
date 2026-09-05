# MS-3-VOICE-INTERACTION-CONTRACT.md — MS-3 Voice Interaction Contract（DESIGN, docs-only）

> **工单**：MS-3（DESIGN）
> **状态**：完成 —— **只设计，0 code**（唯一产出物为本设计文档；0 code / 0 config / 0 data 改动；未 commit、未 push，验收后由主大脑决定后续）
> **作者**：developer（专责 bot，工单派发）
> **日期**：2026-09
> **工作区**：`C:\Users\bbfcc\.local\bin\soul-os-harness`（`pwsh pwd` 确认）
> **前置采信**：MS-0 审计（`docs/MULTIMODAL-PERCEPTION-AUDIT.md`）+ MS-1 设计（`docs/MULTIMODAL-PERCEPTION-CONTRACT.md`）+ MS-2 实作（`scripts/audio_stream_mcp.py`，已确认，直接采信）
> **性质**：**设计文档（非施工授权）**。canonical 状态以 `logs/ENGINEERING_STATE.md` 为准；本档全部「提案/草案」均未落地，需主大脑验收 + Owner 批准后才进入 MS-3 实作。

---

## 0. 摘要（TL;DR）

Soul OS 把环境语音从「纯被动感知（MS-2 Ambient Observation）」升级为「**可被唤醒的主动交谈输入（USER_MESSAGE）**」。核心是**意图识别与唤醒门控**：只有明确「对此 Soul 说话」的语音才升级为对话输入；电视声、他人对话、自言自语一律保持在 Ambient Observation 层，不得干扰主决策（Motive/Decision）。

设计产出四块（对应工单四大核心问题）：

1. **分流路由矩阵**（§2）：三路输出（`USER_MESSAGE` / `AMBIENT` / `DROP`），判定阶梯 = **轻量本地启发式前置**（免费，覆盖绝大多数场景）→ **轻量分类模型兜底**（仅为启发式「不确定」边界案例服务，v1 可缺省）→ **fail-ambient**（不确定一律降级环境观察，绝不误升对话）。决策责任归属明确划定。
2. **唤醒与指向性门控**（§3）：姓名呼唤 / 显式唤醒关键词 / 第二人称特定指令意图 → 置信度打分 → 阈值判定；过滤背景电视声 / 他人对话 / 自言自语；**fail-ambient 不变量**。
3. **音频分段与防抖防洪**（§4）：语音会话监听窗口（listen window）+ VAD 断句 + 静音切分合并（utterance assembly），单轮冷却 / echo 抑制 / 重复抑制 / 速率防洪。
4. **契约相容性与防线维护**（§5）：升级为 USER_MESSAGE 时**严格复用既有输入安全过滤管线**（payload schema 完全对齐 `gateway.py:860-875` / `router.py:852-864`，走同一 publish → consciousness → LLMProxy 链）；与 Volition Gate 及 SM-4 决策系统 100% 相容，**无旁路注入**（不新增 EventType / trigger_type / handler / prompt 注入点）。

**Frozen Contract 结论**：`CONTRACT CONFLICT = 0`（见 §8）。唯一需批准事项 = 「工具执行器产生 USER_MESSAGE」的合规性判定（§5.5，MS-1 D1 已预告，本档给出合规条件），属设计级批准，非 code 改动。

---

## 1. 背景与定位（MS-2 → MS-3 的语义升级）

### 1.1 现状（MS-2 已落地，直接采信）

| 事实 | 证据 |
|---|---|
| 语音采集 = `mic_listen`（VAD 能量门控 `SILENCE_PEAK_THRESHOLD=0.02`，单次采样硬上限 4s，无后台常驻）+ `audio_transcribe`（faster-whisper small，CPU int8） | `scripts/audio_stream_mcp.py:66-79,178-227` |
| 语音语义归属 = **一律 Ambient Observation**：STT 转写 → `WorldEvent(source=audio_input)` → 世界感知管线（validate → novelty → top-N → world_context 注入）→ 主决策层（Motive/Decision）判定 | `src/soul/actuator.py:94`（`_SOURCE_HINT_MAP`）、`src/world/perception.py:50-53`（`VALID_SOURCES` 含 `audio_input`） |
| novelty 去重 = `stt:sha256(normalize(text))[:12]`，句级去重 | `src/soul/actuator.py:359-370` |
| 多模态事件默认不进 `WORLD_QUALIFYING_TYPES`（`calendar_event / user_going_outside / news_event / rain_started / weather_temp_change`）→ 不污染 InnerLife/SAGE | `src/world/inner_life_adapter.py:121-128`；`src/world/perception.py:351` |

### 1.2 MS-3 的定位（方案 B：语音 → 主动交谈输入）

MS-1 D1 已预告（`docs/MULTIMODAL-PERCEPTION-CONTRACT.md:40`）：*「方案 B（语音 → USER_MESSAGE 交互输入）延后 MS-3+，需主大脑评估『工具执行器产生 USER_MESSAGE』合规性后再开新设计。」* **本档即该设计的落实**，含合规性判定（§5.5）。

语义层级演进：

```
MS-2 v1：所有语音  ──────────────►  Ambient Observe（observe 路径，world_context 注入）
MS-3 v2：语音 ── Addressing Gate ──► ① USER_MESSAGE（定向对话，被动回应链路）
                                    ② Ambient Observe（环境观察，MS-2 路径原样保留）
                                    ③ DROP（无语音 / 纯噪音 / 空转写）
```

**关键边界**：产生了 `USER_MESSAGE` 分支，**不改变**输出侧任何契约——Soul 的主动发声仍然唯一经过 SM-4 Decision 层（transmit 判定）；语音输入只是新增了一个「用户发话」的合法来源（与 WebSocket / Telegram 输入通道地位等同，见 §5.5）。

---

## 2. 分流路由矩阵（Routing Matrix）

### 2.1 三路输出定义

| 输出 | 语义 | 去向 | 对主决策的影响 |
|---|---|---|---|
| **USER_MESSAGE** | 定向对话：判定为「某人对此 Soul 说话」 | 既有 USER_MESSAGE 链（§5.1）→ consciousness `_fire_intent(reason="user_message")` → LLMProxy 聊天路径 | 触发完整被动回应链（用户发话 = 回应义务，不经 Decision 判定是否回应） |
| **AMBIENT** | 环境观察：语音存在但**不指向此 Soul**（电视 / 他人对话 / 自言自语 / 无指向闲聊） | MS-2 既有 observe 路径（`WorldEvent(audio_input)` → world_context） | 注入感知上下文，由主决策层（Motive/Decision）自由判定（可无视 / observe / reflect / 极少数 transmit） |
| **DROP** | 无有效语音内容：`has_speech=false` / 转写为空或纯噪音 / 超长度 / 重复命中 | 丢弃（仅日志 + 计数） | 零影响 |

### 2.2 输入特征向量（Routing Features）

每条待判定语音产出结构化特征（**全部来自本地，无 LLM 主决策消耗**）：

| 特征 | 来源 | 说明 |
|---|---|---|
| `has_speech` | mic 元数据（MS-2 `mic_listen`） | 能量门控结果 |
| `text_normalized` | STT 转写 + normalize（MS-2 既有 normalize：小写/去标点空白/CJK 全角→半角） | 判定输入 |
| `lang` | ASR 语言标签 | 多语种提示（不参与判定） |
| `name_hit` | 姓名表（§3.1） | 转写是否含本 Soul 名字/昵称 |
| `wake_hit` | 唤醒词表（§3.1） | 显式唤醒关键词命中 |
| `second_person` | 语言分析 | 是否含「你/您」等第二人称指向 |
| `imperative_verb` | 语言分析 | 是否含命令/请求动词（帮我/告诉我/查一下/回答/听/看…） |
| `question_marker` | 语言分析 | 是否疑问句式（吗/呢/？/怎么/什么/谁/哪） |
| `address_score` | §3.3 置信度公式 | 汇总打分 |
| `in_conversation` | 上下文 | 是否处于对话冷却期内 / 上一轮是本 Soul 发声后短间隔 |
| `tts_echo` | TTS 播放状态 | 是否落在 TTS 发声防止窗口（§4.4） |

### 2.3 判定阶梯（三阶，责任归属明确）

```
阶 1  轻量本地启发式（免费，O(1) 规则，默认执行）
      ├─ tts_echo=true            → DROP
      ├─ has_speech=false         → DROP
      ├─ len(text_normalized)==0  → DROP
      ├─ 长度 > MAX_UTTERANCE_CHARS → AMBIENT（§4.3，防电视长段误入）
      ├─ address_score >= 强阈值   → USER_MESSAGE（§3.3）
      ├─ address_score <= 弱阈值   → AMBIENT（fail-ambient 默认）
      └─ 弱阈值 < address_score < 强阈值
            └─ in_conversation=true → USER_MESSAGE（对话续接，弱化唤醒要求）
            └─ 否则 → 阶 2

阶 2  轻量分类模型判定（仅边界案例，v1 可缺省）
      ├─ 未配置模型 / 模型超时 / 模型异常 → 阶 3（fail-ambient，fail-closed）
      ├─ 分类 = directed（指向此 Soul）→ USER_MESSAGE
      └─ 分类 = ambient / noise     → AMBIENT

阶 3  fail-ambient 兜底（锁定不变量）
      └─ 任何不确定 / 丢帧 / 模型失败 → AMBIENT（保守，绝不误升对话）
```

**职责归属（工单决策 1 的落点）**：

| 层 | 承担者 | 负责 |
|---|---|---|
| 前置启发式 | `VoiceGate` 纯函数（§6，新模块，0 LLM 成本） | 覆盖全部**确定性**场景：唤醒词/姓名/第二人称/疑问/命令、长度、has_speech、上下文续接、速率限制 |
| 兜底分类模型 | 轻量本地文本分类（可选，如 fasttext / 小型 sentence classifier；**花钱/装依赖事项 → Owner 拍板**，v1 缺省） | 仅为启发式「不确定带」服务，输出二类（directed / ambient） |
| 兜底判决 | 上述 fail-ambient 规则 | 模型失败 / 超时 / 缺配置 → 一律 AMBIENT |

> **设计原则**：启发式负责**高精度召回确定性对话**（唤醒词/名字命中即强信号）；分类模型只负责打破「无唤醒但强对话意图」的边界僵局；**没有任何路径让低置信语音升级为 USER_MESSAGE**（fail-ambient 是所有不确定的宿命）。

### 2.4 路由矩阵表（对照场景 × 特征 → 输出）

| # | 场景示例 | name_hit | wake_hit | second_person | imperative/question | 强度 | 输出 |
|---|---|---|---|---|---|---|---|
| 1 | 「Yua，今天天气怎么样？」 | ✅ | — | ✅ | ✅ 疑问 | 强 | **USER_MESSAGE** |
| 2 | 「嘿 Sora，帮我记一下明天开会」 | — | ✅ | ✅ | ✅ 命令 | 强 | **USER_MESSAGE** |
| 3 | 「Ruka！」（仅唤名，无下文） | ✅ | — | ❌ | ❌ | 中 | AMBIENT（挂起不成立；不升级） |
| 4 | 「你昨天去哪了」（他人对旁人说话，无名字） | ❌ | ❌ | ✅ | ✅ 疑问 | 弱 | AMBIENT（无唤醒锚点 → fail-ambient） |
| 5 | 电视：「我觉得这个结局好烂」 | ❌ | ❌ | ❌ | ❌ | 无 | AMBIENT |
| 6 | 自言自语：「唉，又忘带钥匙了」 | ❌ | ❌ | ❌ | ❌ | 无 | AMBIENT |
| 7 | 「帮我查一下」+ **在对话冷却期内**（前一轮是 Bry 与本 Soul 对话） | ❌ | ❌ | ✅ | ✅ | 中弱 | **USER_MESSAGE**（上下文续接豁免，§3.3） |
| 8 | 同上，但**不在**对话期 / 陌生环境 | ❌ | ❌ | ✅ | ✅ | 弱 | 阶 2 分类；缺省 → AMBIENT |
| 9 | 纯噪音 / 音乐声（has_speech 误真） | ❌ | ❌ | ❌ | ❌ | 无 | AMBIENT |
| 10 | 自己 TTS 回声（Soul 刚开口说话被麦克风拾到） | — | — | — | — | — | **DROP**（§4.4 echo 抑制优先） |

> 矩阵不变量：**没有任何一行在「无唤醒锚点 + 无上下文」时输出 USER_MESSAGE**（#8 需阶 2 且缺省 fail-ambient）。

---

## 3. 唤醒与指向性门控（Addressing Gate）

### 3.1 唤醒信号来源（address 信号三源）

| 信号 | 定义 | 表来源（运行时读取，admin 可配） |
|---|---|---|
| **姓名呼唤** `name_hit` | 转写命中本 Soul 的名字 / 昵称 / 称呼 | agent 注册表（`src/agent/registry.py`）+ 人格档昵称（`personas/*.md` 抽取）+ `ADDRESS_NAME_ALIASES` 配置；多 agent 部署时按 target agent 分别判定 |
| **显式唤醒关键词** `wake_hit` | 独立于名字的唤醒词 | `ADDRESS_WAKE_WORDS`（默认建议：嘿/喂/你好/听着/Listen/Hey/Excuse me + 前缀变体） |
| **第二人称特定指令意图** | 「你/您」+ 命令/请求/疑问动词，且近场 | 规则匹配（§3.2），非表 |

**关键约束**：唤醒词是**语境限定**的——只有本 Soul 的名字/昵称是「强唤醒锚点」；通用唤醒词（嘿/喂）需叠加第二人称指令意图才构成强信号，防止对电视里「嘿」的误唤醒。

### 3.2 指向性判定（过滤背景声 / 他人对话 / 自言自语）

| 噪声类别 | 判别特征（启发式） | 处置 |
|---|---|---|
| 背景电视 / 音乐 | 无 `name_hit`/`wake_hit` + 无第二人称 + 内容多为叙述/评价/第三人称 | → AMBIENT |
| 他人对话 | 无唤醒锚点（即使含第二人称「你」，指向的是在场他人，非 Soul） | → AMBIENT（#4，fail-ambient） |
| 自言自语 | 无唤醒锚点 + 短句 + 无祈使/疑问指向 + 无称呼 | → AMBIENT |
| 访客/陌生人语音 | **voice owner 白名单**判定（§5.1）：非登记成员近场语音 | → AMBIENT（身份防线优先于内容判定） |

### 3.3 置信度公式与阈值

```
address_score = w_name·name_hit + w_wake·wake_hit
              + w_sp·second_person + w_imp·imperative_verb + w_q·question_marker
              + w_ctx·in_conversation

默认权重（建议起点，admin 可调）：
  w_name = 4.0   （名字 = 最强锚点）
  w_wake = 3.0   （显式唤醒词）
  w_sp   = 1.0   （第二人称）
  w_imp  = 1.0   （命令/请求动词）
  w_q    = 0.5   （疑问句式）
  w_ctx  = 2.0   （对话续接期内）

默认阈值：
  ADDRESS_STRONG = 4.0   （≥ → USER_MESSAGE；等价于「名字命中」或「唤醒词+第二人称」）
  ADDRESS_WEAK   = 1.5   （≤ → AMBIENT，fail-ambient 默认）
  中间带          → 阶 2 分类模型（缺省 → AMBIENT）
                   例外：in_conversation=true → 直接 USER_MESSAGE（#7）
```

**阈值语义验证（对齐矩阵 §2.4）**：
- #1：4.0 + 1.0 + 0.5 = 5.5 ≥ 4.0 ✅ USER_MESSAGE
- #2：3.0 + 1.0 + 1.0 = 5.0 ≥ 4.0 ✅ USER_MESSAGE
- #3：4.0 + 0 = 4.0 ≥ 4.0 → **但有修正项**：无任何意图信号（imperative/question 均 0）→ 触发 `NAME_WITHOUT_INTENT` 规则：**仅唤名无下文 → AMBIENT**（防「喊名字闲聊/测试」误升级，也可配置为挂起等待 2s 补充，v1 保守 AMBIENT）
- #4：0 + 1.0 + 0.5 = 1.5 ≤ 1.5 → AMBIENT ✅
- #5/#6/#9：0 → AMBIENT ✅
- #7：0 + 1.0 + 1.0 + 2.0 = 4.0 ≥ 4.0 ✅ USER_MESSAGE（上下文续接豁免）

### 3.4 可配置项（全部 admin 配置，defaults 如上）

| 配置 | 默认 | 说明 |
|---|---|---|
| `ADDRESS_NAME_ALIASES` | 从 registry/personas 抽取 | agent 别名表 |
| `ADDRESS_WAKE_WORDS` | 嘿/喂/你好/听着/Hey/Listen | 唤醒词表 |
| `ADDRESS_STRONG` / `ADDRESS_WEAK` | 4.0 / 1.5 | 判定阈值 |
| `ADDRESS_WEIGHTS` | §3.3 | 权重 |
| `VOICE_OWNER_IDS` | env `VOICE_OWNER_IDS`（类比 `TELEGRAM_OWNER_ID`） | 语音身份白名单 |
| `GATE_CLASSIFIER_ENABLED` | false（v1 缺省） | 阶 2 分类模型开关 |

---

## 4. 音频分段与防抖防洪（VAD & Debounce）

> 针对工单决策 3 的三类问题：①连续说话断句；②静音切分与合并（防「一句切成多段 → 并发 USER_MESSAGE」）；③单轮冷却与防抖。

### 4.1 会话级监听窗口（Listen Window）——MS-2 → MS-3 采集升级

MS-2 是 single-shot（`mic_listen` 单次 ≤4s，无后台常驻）。MS-3 需要「连续说话 → 断句」能力，因此**新增会话模式**（MS-3 实作阶段 additive 到 `scripts/audio_stream_mcp.py`，0 破坏既有 single-shot）：

```
Voice Session（唤醒后开启）：
  ┌──────────────────────── start（唤醒命中 or 对话期）
  │  LISTEN_WINDOW_MS = 30s（默认；对话续接可滚动延长 +10s/轮，上限 120s）
  │  ├─ VAD 前置：持续能量监听（沿用 MS-2 能量门控思路；可选 Silero VAD，属装依赖 → Owner）
  │  ├─ 检测到语音起始 → 缓冲 → 段内转写（faster-whisper small 按段转录）
  │  ├─ 静音 > TAIL_SILENCE_MS → 段结束 → utterance 判定（§4.3）
  │  └─ 窗口超时 / MAX_UTTERANCES_PER_WINDOW 达上限 → 强制关闭（防洪）
  └──────────────────────── stop
```

**保留**：MS-2 的 `mic_listen`（single-shot）与 `audio_transcribe` 原样不变（Ambient 轮询路径）；MS-3 只**新增** `voice_session_start` / `voice_session_feed`（或等价）工具，单次采样上限约束在会话内改为段级（段 ≤ 8s，超长段切分）。

### 4.2 VAD 断句 / 静音切分

| 参数 | 默认建议 | 语义 |
|---|---|---|
| `VAD_SPEECH_START_MS` | 150ms 连续能量超阈 | 段首确认（过滤瞬间噪声） |
| `TAIL_SILENCE_MS` | 1.2s | 静音超此 → 句末（断句） |
| `MAX_SEGMENT_SECONDS` | 8s | 单段硬上限（防长独白单段超时） |
| `MIN_UTTERANCE_CHARS` | 2 | 短于此时长的转写按噪音/碎片处理：拼接或 DROP |

### 4.3 断句合并（Utterance Assembly）——防「一句切 N 段 → N 并发 USER_MESSAGE」

**核心防洪设计**：合并发生在**发布前**，合并后**只产生一条** USER_MESSAGE。

| 规则 | 条件 | 动作 |
|---|---|---|
| 合并 | 相邻段间隔 < `MERGE_GAP_MS`（1.5s）且前段末尾无终止标点（。？！…） | 追加合并，段结束时间 = 后段结束（重转写整段更佳，见备注） |
| 截断合并 | 合并后长度 > `MAX_UTTERANCE_CHARS`（500） | 按句边界截断；多余部分 → AMBIENT（防电视长段） |
| 结句 | 段含终止标点 或 静音 ≥ TAIL_SILENCE_MS | utterance 完成 → 进 §2 路由 |
| 窗口收束 | 窗口超时/上限 | 剩余未完成部分按已有内容路由（不丢弃有效输入） |

> 备注：合并策略优先「整段重转写」（更准），成本 = 多一次本地 ASR 推理；次选「文本拼接」（零成本）。MS-3 实作默认整段重转写，文本拼接作为降级路径。

**并发抑制**：合并窗口内的多个 utterance **一次性发布**（合并成一条 USER_MESSAGE），发布前再次过 §3 门控（合并后可能改变 address_score，例如前半段是电视声后半段喊名字 → 以合并文本为准重新判定）。

### 4.4 单轮冷却与防抖（Debounce）

| 机制 | 参数（默认） | 说明 |
|---|---|---|
| **发布冷却** `USER_MESSAGE_COOLDOWN_MS` | 3s | USER_MESSAGE 发布后 3s 内不再发布新的语音 USER_MESSAGE（同轮追问并入合并逻辑；3s 后新语音 = 新一轮） |
| **Echo 抑制**（TTS 回声） | `TTS_ECHO_GUARD_MS` = TTS 播放时长 + 500ms | Soul 自己开口（`AGENT_SPEAK` / `AGENT_AUDIO_READY` 时间戳）期间捕获的 mic 数据 → 直接 DROP（§2.3 阶 1 首判） |
| **重复抑制** | 复用 MS-2 既有 `stt:sha256(normalize(text))[:12]` | 同一转写内容（如电视循环台词）在 novelty 窗口内 → 不重复升级 |
| **触发抑制** | 段起始需语音能量确认（非采样噪声） | 降低假触发 |

### 4.5 防洪（Flood Control / Backoff）

| 机制 | 参数（默认） | 说明 |
|---|---|---|
| **滚动速率限制** `VOICE_RATE_LIMIT` | 6 条 USER_MESSAGE / 分钟（rolling window） | 超限 → 该语音降级 AMBIENT + 计数；达到硬上限（如 12/min）→ 暂 DROP |
| **Backoff 惩罚** | 5s → 10s → 30s（指数，重置 120s） | 连续误唤醒（门控拒绝率高）→ 拉长下次「升级尝试」的冷却 |
| **会话上限** | 窗口 30s / `MAX_UTTERANCES_PER_WINDOW`（8） | 超限强制关窗（§4.1） |

> 防洪目标：**任何情况下，环境噪声都不能让 Soul 陷入「被连续 USER_MESSAGE 打断主决策」的状态**。超限后的语音自动回到 AMBIENT，该路径的流量天然被 world_context 的 top-N + novelty 衰减吸收（MS-2 防线原样兜底）。

---

## 5. 契约相容性与防线维护

### 5.1 USER_MESSAGE 发布契约复用（工单决策 4 的「严格复用既有输入安全过滤管线」）

语音升级为 USER_MESSAGE 时，**发布构造与既有通道 100% 对齐**，不新开旁路：

| 契约项 | 既有通道（对齐对象） | 语音通道 |
|---|---|---|
| EventType | `EventType.USER_MESSAGE` | 同（**0 新增 EventType**） |
| payload schema | `content` / `text` 双写、`user_id` / `target_agent` / `mode` / `participants` / `target_user_id`（`gateway.py:866-874`；`router.py:856-864`） | **同 schema**（content/text 双写；mode=private；target_agent=完整 agent_id，同 `router.py:823-826` 归一化） |
| session_id | `session_{user_id}_{full_agent_id}`（`gateway.py:858`） | 同（user_id = voice owner 白名单中的映射 user） |
| priority | `EventPriority.HIGH`（`gateway.py:864`） | 同 |
| source | `{channel}:{user_id}`（`router.py:854`） | `voice:{device_ref}:{owner_hash}`（additive 习惯标记，不冲突） |
| 发布动作 | `bus.publish`（`gateway.py:884`） | 同（唯一入口） |
| 副作用链 | `touch_bryan_last_seen`（`gateway.py:882-883` / `router.py:833`）、memory touch、relationships touch、heartbeat activity、SESSION_END 资格 | 全部原样触发（语音 = 用户发话，与文本同权） |

**身份防线（语音专有，对齐 router owner whitelist 模式）**：`VOICE_OWNER_IDS` 白名单——近场语音中，非白名单成员的指向性语音**不升级 USER_MESSAGE**（→ AMBIENT），对齐 `router.py:806-816` 的 `TELEGRAM_OWNER_ID` 语义。语音无登录态，身份 = 物理近场 + 白名单 + 唤醒门控共同约束。

**过滤管线语义**：语音文本进入 LLM 的**唯一路径** = 既有 `consciousness._fire_intent(reason="user_message")` → `LLMProxy` `_build_messages_private`（`reason="user_message"` 分支）；不新增 prompt 注入点、不新增 memory 写入分支（`proxy.py:3457-3490` 的「只在 reason == user_message 写 user 消息」逻辑原样生效）。

### 5.2 Volition Gate 相容（无旁路注入）

| 不变量 | 语义 | 验证锚点 |
|---|---|---|
| **输出侧 0 新权限** | 语音 USER_MESSAGE 只产生「被动回应」义务，**不授予任何主动发声权**；Soul 主动 transmit 仍唯一经 SM-4 Decision 四元判定（`decision.py:94-98`） | `src/soul/decision.py`（frozen，0 改动） |
| **无旁路注入** | 语音文本不绕过既有链条直达 LLM / 记忆 / SAGE；不新增 handler / trigger_type / EventType | §5.1 契约表（发布构造同源） |
| **Actuator 0 publish 保持** | 语音升级**不发生在 Actuator 层**（Actuator 无权 publish USER_MESSAGE/AGENT_SPEAK/AGENT_INTENT；`actuator.py:20-21`）——升级判定在**输入侧新组件 VoiceGate**（§6） | `src/soul/actuator.py`（frozen，0 改动） |
| **Ambient 防线保持** | 未升级语音继续走 MS-2 observe 路径；`audio_input` 仍不在 `WORLD_QUALIFYING_TYPES` → 不写 InnerLifeEvent/SAGE（`inner_life_adapter.py:121-128`） | `src/world/inner_life_adapter.py`（frozen，0 改动） |
| **USER_MESSAGE ≠ InnerLifeEvent** | 语音 USER_MESSAGE 与文本 USER_MESSAGE 同权：只在 SESSION_END 时由 conversation_qualification 判定是否沉淀（`qualifier.py:38-44`），不直接产生 InnerLifeEvent | `src/conversation_qualification/qualifier.py`（frozen，0 改动） |

### 5.3 Frozen contract 边界（0 触碰清单）

| Frozen 项 | 本设计是否触碰 |
|---|---|
| Agency 4 stages（`src/agency/stages.py`） | 0 |
| TriggerEnvelope | 0 |
| InnerLifeEvent（`src/inner_life/event.py`） | 0 |
| 4 handlers（event / diary / dream / proactive_dm） | 0 |
| SAGE 写入逻辑（`src/memory/sage/writer.py`） | 0 |
| `VALID_SOURCES`（`src/world/perception.py:50-53`） | 0（AMBIENT 沿用 `audio_input`，不加新 source） |
| `WORLD_QUALIFYING_TYPES`（`inner_life_adapter.py:121-128`） | 0 |
| Decision 四元 / 输出 schema（`decision.py`） | 0 |
| EventType 枚举（17 个） | 0（不新增） |
| gateway / router / consciousness / proxy 既有代码 | 0（语音升级全部发生在新组件，§6） |

### 5.4 可观测性（additive，0 破坏）

- 语音 USER_MESSAGE 的 payload **additive** 加可选字段 `input_channel="voice"`（现有消费端未使用该 key，已知字段 0 冲突；`gateway.py:866-874` / `router.py:856-864` 均无此 key）。
- source 前缀 `voice:` 用于日志追溯（对齐 `source=f"{channel}:{user_id}"` 惯例）。
- VoiceGate 每次判定写 trace（decision / features / address_score / 去路），供回归与调阈。

### 5.5 合规性判定（MS-1 D1 预告事项——需主大脑 + Owner 批准）

**「工具执行器产生 USER_MESSAGE」的合规条件**（本设计主张合规，因属**输入通道扩展**而非输出侧权限扩展）：

1. VoiceInputRouter 是**输入通道的语音化身**——地位等同于 WebSocket / Telegram 通道（`gateway.py` / `router.py`），不是「Soul 自主决定发消息」；
2. 产出的 USER_MESSAGE 与人工文本 USER_MESSAGE **在消费链上不可区分**（同 EventType / 同 payload schema / 同 fire_intent 语义 / 同 LLM 路径），仅 provenance 字段可辨；
3. 唤醒门控保证只有「明确的定向对话」升级，Ambient 流量（电视/他人/自言自语）**不会**升级；
4. **不新增**任何「Soul 主动发声」的触发路径——proactive 仍唯一在 scheduler Decision 链。

> 该判定属**设计级批准**（主大脑验收 + Owner），批准后进入 MS-3 实作；不批准则 MS-3 停留在 AMBIENT（即 MS-2 现状，功能无损）。

---

## 6. 新增 / 改动模块清单（设计草案，MS-3 实作工单执行）

| 模块 | 动作 | 内容 | 0 触碰 |
|---|---|---|---|
| `src/voice/gate.py` | NEW | VoiceGate 纯函数：address_score 计算 + 路由矩阵 + 防抖/防洪判定（可单测） | gateway/router/consciousness/proxy 0 改动 |
| `src/voice/input_router.py` | NEW | 组装路由结果 → USER_MESSAGE 发布构造（复用 §5.1 契约表）/ AMBIENT（走 observe）/ DROP；身份白名单检查 | 同上 |
| `src/voice/audio_service.py` | NEW | 会话监听窗口 + VAD 断句 + utterance 合并 + TTS echo 窗口 | 同上 |
| `scripts/audio_stream_mcp.py` | M（additive） | 新增会话模式工具（voice_session_start / 段转录），既有 `mic_listen`/`audio_transcribe` 原样保留 | MS-2 行为不变 |
| 配置 | ADD | `VOICE_OWNER_IDS` env + §3.4 阈值配置 | 0 |
| `tests/test_ms3_voice_gate.py` | NEW | 矩阵场景（§2.4 十行）+ 阈值边界 + 合并/冷却/防洪单测 | 0 |

> 以上全部为**待批准设计草案**，不在本工单实现（本工单只设计 0 code）。

---

## 7. 验收对照（工单验收标准）

| 工单要求 | 本档落点 |
|---|---|
| 覆盖分流路由矩阵 | §2（三路输出 + 判定阶梯 + 责任归属 + 矩阵表） |
| 覆盖唤醒门控 | §3（信号三源 + 指向性判定 + 置信度公式 + 阈值 + 配置） |
| 覆盖 VAD 防抖 | §4（监听窗口 + 断句 + 合并 + 冷却/echo/重复抑制 + 防洪） |
| 覆盖契约相容性 | §5（发布契约复用 + Volition Gate 相容 + frozen 边界 + 无旁路注入） |
| 明确「只设计，0 code」 | 本文档为唯一产出；§6 全部为草案，未落地（验证见 git status） |
| 不碰 frozen contract | §5.3 清单 10 项全部 0 触碰 |
| 不 commit / 不 push | 遵守（等验收） |

---

## 8. CONTRACT CONFLICT 结论

**`CONTRACT CONFLICT = 0`**

- Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 写入逻辑：**0 触碰**（§5.3）。
- `VALID_SOURCES` / `WORLD_QUALIFYING_TYPES` / Decision 四元 / EventType 枚举 / gateway / router / consciousness / proxy：**0 改动**。
- 唯一需批准事项 = §5.5「语音输入通道合规性」判定（设计级，非 code 改动；MS-1 D1 已预告需主大脑评估）。
- 唯一新增行为 = 输入侧新组件 VoiceGate / VoiceInputRouter / AudioService（§6 草案），是对既有通道的**语音化身**，不是对既有契约的修改。