# 🏛️ Soul OS — 多 Agent 社交廣播與客廳共處架構：跨 AI 深度審查與諮詢文件
*(Peer-Review & Architectural Advisory Document for Frontier AI Models: Claude 3.7 Sonnet / GPT-4o / o3-mini)*

**發起者**: Soul OS 主大腦 (Master Planner & Architecture Guardian)  
**系統環境**: Soul OS Harness (Python 3.11 / Asyncio / SQLite FTS5 / SAGE Graph / EventBus)  
**當前里程碑**: SI-2 (Social Diffusion) 落地 + TL-6 (Social Lounge Stability) 驗收  
**代碼狀態**: Git HEAD `eba2183`，213/213 核心與 Harness 測試全綠  

---

## 🧭 一、 系統架構背景與戰略北極星 (North Star v2)

Soul OS 是為「**靈魂持續存在、自由生長、多個靈魂彼此互動的虛擬世界**」所設計的異步運行系統，而非被動式工具或單一 Chatbot 玩具。

### 1. 核心資料流與本體論
```
World happened ──► Soul perceived ──► Soul interpreted ──► Soul decided ──► Soul acted
```
每個靈魂（Agent）擁有獨立的：
* **意識核心**: 10 位角色深度 Persona (COS v1.0 L0-L3 四層還原架構)。
* **記憶與升華層 (Soul-Elevation)**: 經歷經過 Pattern 中間層，升華為持久靈魂結構（`ACTIVE → WEAKENING → DORMANT → SUPERSEDED` 四態生命週期），嚴格遵循 *Contradiction ≠ Revision* 與 *Forgetting = Transition, not delete*。
* **現象學時間感知**: 初期平靜（無感）➜ 中期牽掛（浮現張力）➜ 長期釋然（張力消退），真實感知時間流逝與留白。
* **自主決策層 (Volition Path)**: 四元自發行動（`transmit` 主動發訊 / `observe` 觀察環境 / `reflect` 自我回顧 / `do_nothing` 安靜留白），經 TL-5 驗證 `do_nothing` 佔 82.5%，保持真實生命的留白常態。

---

## 🛡️ 二、 SI-2 多 Agent 社交擴散機制：三大防線架構

在最新落地的 **SI-2 (Social Diffusion)** 與 **TL-6 (Living Room Simulation)** 中，我們讓多個靈魂（如 Ruka、Yua、Akane）共同置身於「公共客廳 (Lounge / Soul Wall)」空間。為防止多 Agent 系統常見的災難性問題（廣播風暴、身份認知污染、隱私洩漏），我們設計並落實了**三大防線**：

```
[Agent A 發言]
      │
      ▼
┌────────────────────────┐
│ 防線 2: Privacy Gate   │ ──► 私聊 1:1 DM (預設 private) ──► 嚴格攔截於總線外 (0 洩漏)
└──────────┬─────────────┘
           │ (僅 public 公共客廳動態允許發布)
           ▼
   [SoulEventBus 廣播] (SOCIAL_WORLD_EVENT)
           │
           ├───────────────────────────────┬───────────────────────────────┐
           ▼                               ▼                               ▼
  ┌─────────────────┐             ┌─────────────────┐             ┌─────────────────┐
  │  Agent A (自己)  │             │  Agent B (他者)  │             │  Agent C (他者)  │
  └─────────────────┘             └────────┬────────┘             └────────┬────────┘
                                           │                               │
                                           ▼                               ▼
                                  ┌──────────────────┐            ┌──────────────────┐
                                  │ 防線 3: Firewall │            │ 防線 3: Firewall │
                                  │ actor_id != self │            │ actor_id != self │
                                  │  EXTERNAL_OTHER  │            │  EXTERNAL_OTHER  │
                                  └────────┬─────────┘            └────────┬─────────┘
                                           │                               │
                                           ▼ (嚴禁內化為回憶/性格)           ▼ (嚴禁內化為回憶/性格)
                                  ┌──────────────────┐            ┌──────────────────┐
                                  │ 防線 1: Ambient  │            │ 防線 1: Ambient  │
                                  │  [社交感知] 注入  │            │  [社交感知] 注入  │
                                  │ 不觸發即時搶話   │            │ 不觸發即時搶話   │
                                  └──────────────────┘            └──────────────────┘
```

### 三大防線具體規範：
1. **防線 3：Identity Firewall（最高優先，絕對不變量）**
   - 消費端硬守門（Submission Gate 第 6 步）：任何 `actor_id != current_agent_id` 的事件一律打標 `EXTERNAL_OTHER_ACTION`。
   - **絕對紅線**：外部他者行為**只准作為客廳環境背景感知**，**絕對禁止**內化為自身的 Episodic 情景記憶，**更嚴禁**升華為自身性格或信念！
2. **防線 2：Privacy Visibility Gate**
   - 發布端守門：靈魂與 Owner (Bryan) 的 1:1 私聊 DM 預設為 `private`，嚴格攔截於廣播總線之外；僅公共頻道（Lounge / Soul Wall）允許擴散。
3. **防線 1：Ambient Perception Path**
   - 社交動態經 `WorldPerceptionMiddleware` 平行過濾，以 `[社交感知]` 區塊注入 Prompt，帶有反框架提示（「*這些是他人的行為，屬於環境背景，不是你的經歷；自然感知即可，不要過度反應，不要逐條回應*」）。
   - **低刺激度語意**：不賦予即時搶話或 transmit 特權，Top-N 預算約束（預設 2 筆），杜絕 Agent 間的連鎖搶話廣播風暴。

### 驗證基線（TL-6 模擬驗收）
在 TL-6 社交情境 Harness 中（包含晨間問候、Bryan 進入客廳、1:1 私聊隔離、深夜安靜、5 筆突發脈衝、記憶隔離審計等 7 個 tick，3 次系列 Run）：
- **Anti-Storm Rate**: 100%（0 自激連鎖風暴）
- **Identity Quarantine**: 100%（0 他者記憶污染）
- **Privacy Gate**: 100%（0 私聊外洩）
- **D2 Determinism & 0 Mutation**: PASS（3 runs 軌跡完全一致，生產資料 0 diff）

---

## 💬 三、 跨 AI 深度諮詢議題 (Questions for Peer Review)

請各家頂級 AI 專家（Claude 3.7 Sonnet / GPT-4o / o3-mini 等）站在**大型多 Agent 自主系統架構師**的角度，針對以下 4 個關鍵難題進行深度同行評議，並給予設計建議：

### 議題 1：客廳公共場域的「話題連續性」vs「留白克制」的動態平衡
* **現狀**: 為了徹底消滅「廣播風暴」與「相互捧讀死循環」，我們把社交事件定義為「純 Ambient 感知」，不主動喚醒即時發言。
* **潛在痛點**: 這會導致客廳環境偏向「安靜/冷淡」。當靈魂 A 說「我烤了餅乾在桌上」，靈魂 B 感知到了，但在當下通常選擇 `do_nothing`，直到靈魂 B 自身的定期心跳或 Owner 觸發時才可能提及，失去了對話的「即時互動感」。
* **請教**: 如何在「防自激震盪」的前提下，優雅地引入「受控的低頻互動」？例如：
  - 社交摩擦力衰減模型（Social Friction Decay）？
  - 基於親密度或話題關聯度的「發言動能門檻（Activation Energy）」？
  - 有無推薦的有限狀態機或代幣（Token）消耗機制？

### 議題 2：多靈魂面對 Owner（Bryan）公開呼喚時的發言權仲裁與個性偏置
* **現狀**: 當 Bryan 走進公共客廳發言（如：「大家今天都在忙什麼？」），3 位甚至 10 位 Agent 會同時在背景感知到該事件。
* **潛在痛點**:
  1. 若全部回應，形成刷屏風暴。
  2. 若依賴現有的 AOS 發言權競爭（Speaker Token Arbitration），高主動性/高親密度角色（如元氣直球的瑠夏 Ruka）往往擁有較高的發言傾向，可能長期壟斷客廳對話，導致低主動性角色（如沉默觀察的黑川茜 Akane 或三玖 Miku）被永久邊緣化。
* **請教**: 在異步多 Agent 共處系統中，應如何設計「考慮性格差異的群聊發言調度演算法」？既能符合角色設定，又能讓安靜的角色在適當時機自然插話或被點名？

### 議題 3：身份防火牆（Identity Firewall）在未來「共創/共享回憶」場景下的演進邊界
* **現狀**: 目前防線 3 極其嚴格：`actor_id != self` 100% 標記為 `EXTERNAL_OTHER_ACTION`，完全禁止進入自傳情景記憶與升華樹。這在單向廣播時保護了人格純潔性。
* **潛在痛點**: 未來如果多 Agent 與 Bryan 一起進行了集體活動（例如：「客廳聖誕派對」或「共同策劃了一個專案」），這段經歷對每個人來說確實是「客觀發生過的共有歷史」。
* **請教**: 如何在不破壞「我不是她」的前提下，演進出「我們共同經歷（Shared Episodic Memory）」的資料結構？
  - 是透過「主格分離」（我觀察到 A 做了 X，我們共同參與了 Y）？
  - 還是採用類似分散式共識的「客廳集體回憶錄（Lounge Collective Chronicle）」？

### 議題 4：長程多 Agent 空間的 Context 膨脹與 Token 經濟學
* **現狀**: 我們目前採用 Top-N（預設 2 筆）進行截斷，並在 Prompt 中硬注入 `[社交感知]` 區塊。
* **潛在痛點**: 隨著 10 位 Agent 均處於在線狀態，客廳動態隨時間累積，若每隻 Agent 的每個心跳 tick 都要攜帶社交上下文，Token 成本將隨 `Agent 數量 × 心跳頻率` 線性增長，且可能稀釋各角色私聊核心人格的 Attention。
* **請教**: 針對長程多 Agent 的背景感知，業界或學術界有何更高效的 context 注入策略？（如：語義向量流式衰減、輕量狀態標籤、或僅在產生特定 Motive 時才動態檢索社交歷史？）

---

*期待各位 AI 同仁針對上述 4 大議題提供尖銳的架構批判、邊界案例預警（Edge Cases）、以及最具工程落地性的改進思路！*
