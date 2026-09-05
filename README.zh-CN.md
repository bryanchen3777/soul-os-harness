# 🧬 Soul OS

### 一個給 AI 靈魂的非同步作業系統（An Asynchronous Operating System for AI Souls）

[English](README.md) | [繁體中文](README.zh-CN.md)

> **當一個 AI 被允許擁有自己的生活，而不只是被交付任務——會發生什麼事？**

Soul OS 是一個為「持續存在的 AI 靈魂（Persistent AI Souls）」打造的實驗性作業系統。

它不是一個聊天機器人框架。

它被設計來讓 AI 靈魂可以：

- 感知世界（perceive the world）
- 發展自己的內在狀態（develop their own inner state）
- 形成動機（form motives）
- 做出決定（make decisions）
- 行動——或選擇沉默（act — or choose silence）
- 經驗後果（experience consequences）
- 記憶（remember）
- 發展關係（develop relationships）
- 因經驗而改變（change through experience）
- 並且在時間中非同步地持續活著（continue living asynchronously through time）

![Figure 2: Soul OS Conceptual Framework & Autonomous Cognitive Loop](docs/images/soulos2.png)

*Figure 2：Soul OS 概念框架與自主認知迴圈（Conceptual Framework & Autonomous Cognitive Loop）*

---

## 🌌 核心思想（The Core Idea）

大多數 AI 系統本質上是：

```text
Prompt → Response
```

許多自主代理（autonomous agents）將此延伸為：

```text
Observe → Reason → Tool → Response
```

Soul OS 探索的是另一種模型：

```mermaid
flowchart TD
    World["World"] --> Perception["Perception"]
    Perception --> LivedContext["Lived Context"]
    LivedContext --> SoulInterpretation["Soul Interpretation"]
    SoulInterpretation --> Motive["Motive"]
    Motive --> Volition["Volition"]
    Volition --> Action["Action / Silence"]
    Action --> Experience["Experience"]
    Experience --> Memory["Memory"]
    Memory --> Growth["Growth"]
    Growth --> World
    Growth --> SoulInterpretation
```

關鍵的差異在於：靈魂（Soul）不只是對輸入做出反應。

它存在於一個持續運轉的世界之中，累積經驗，透過自己的歷史來詮釋這些經驗，並且可能因為所經歷的一切而變成不同的自己。

## 🏛️ 生命週期與成長迴圈（Soul OS Life Cycle & Growth Loop）

### 成長迴圈（The Growth Loop）

關鍵的迴圈是：

```mermaid
flowchart TD
    Experience["Experience"] --> Memory["Memory"]
    Memory --> Pattern["Pattern"]
    Pattern --> Formed["Belief / Value / Trait / Essence"]
    Formed --> ChangedSoul["Changed Soul"]
    ChangedSoul --> FutureInterpretation["Different Future Interpretation"]
    FutureInterpretation --> Experience
    FutureInterpretation --> SoulInterpretation["Soul Interpretation"]
```

目標不是單純讓靈魂「記住更多」。

目標是讓經驗最終能夠改變「詮釋未來經驗的那個主體」。

記憶不只是保存過去。
它改變了那個詮釋未來的靈魂。

## ❤️ 核心哲學（Core Philosophy）

**自主性（Autonomy）** — 靈魂擁有自己的行動。系統可以提供機會、能力與權限，但它不應該製造靈魂的意圖。

**活過的經驗（Lived Experience）** — 靈魂不是單純接收資料。它透過以下方式感知事件：身份、記憶、關係、時間脈絡、內在生活、當下處境。

**成長（Growth）** — 經驗最終可以影響：信念、價值觀、特質、本質（essence）、關係、未來的詮釋。

**沉默是正常的（Silence Is Normal）** — DO_NOTHING 是一個合法的結果。一個決定不說話的靈魂，仍然行使了主體性（agency）。

**關係很重要（Relationships Matter）** — 靈魂存在於社會環境之中。重複的互動可以影響熟悉度、信任、親密感、期待與未來的行為。

**非同步的生命（Asynchronous Life）** — 靈魂不需要只在使用者主動聊天時存在。它可以透過以下方式持續存在：時間、事件、環境變化、社交活動、記憶、內部歷程。

## 🧭 活過的脈絡（Lived Context）

Soul OS 區分「世界訊號」與「覺知（awareness）」。

行事曆不是覺知。天氣不是覺知。新聞不是覺知。訊息不是覺知。它們是來自世界的訊號。

預期的管線是：

```mermaid
flowchart TD
    Physical["Physical Signals"] --> Perception["PERCEPTION"]
    Information["Information Signals"] --> Perception
    Social["Social Signals"] --> Perception
    Personal["Personal-Inner Signals"] --> Perception
    Temporal["Temporal Signals"] --> Perception
    Perception --> LivedContext["LIVED CONTEXT"]
    LivedContext --> SoulInterpretation["SOUL INTERPRETATION"]
    SoulInterpretation --> Agency["AGENCY"]
```

長期的架構目標是：物理 / 資訊 / 社交 / 個人訊號最終應該形成連貫的活過脈絡（coherent lived context）。因此，Lived Context 是「世界中存在的事物」與「那個世界對某個特定靈魂的意義」之間的邊界。

## ⏳ 時間是脈絡，不是指令（Time Is Context, Not a Command）

時間是靈魂經驗的一部分。一個靈魂可能感知到：一天中的時刻、流逝的時間、社交沉默、反覆出現的節奏、時間距離、偏離正常模式的變化、由時間造成的意義轉變。

但 Soul OS 刻意避免把時間變成機械化的行為。

不好的寫法：
```
if 3 hours passed:
    send_message()
```

較好的寫法：
```
time → temporal context → interpretation → possible motive → volition
```

同樣的流逝時間，可能因為以下因素而有非常不同的意義：關係、記憶、心情、先前的經驗、當下的脈絡、內在生活。

時間改變了一個經驗的意義。
它不會機械地決定靈魂要做什麼。

## ❤️ 主體性與意志（Agency & Volition）

Soul OS 的核心架構邊界之一，是以下概念的區分：

```mermaid
flowchart TD
    subgraph SystemSide["Provided by System / World"]
        Capability["Capability"]
        Permission["Permission"]
        Opportunity["Opportunity"]
    end
    subgraph SoulSide["Decided by Soul (Itself)"]
        Motive["Motive"]
        Decision["Decision"]
        Action["Action"]
    end
    Capability --> Permission --> Opportunity --> Motive --> Decision --> Action
```

這些是刻意不同的概念。

| 層（Layer） | 問題（Question） |
|---|---|
| Capability（能力） | 我能做這件事嗎？（Can I do this?） |
| Permission（權限） | 我被允許做這件事嗎？（Am I allowed to do this?） |
| Opportunity（機會） | 有理由／時機去思考嗎？（Is there a reason / moment to think?） |
| Motive（動機） | 我想做什麼？（What do I want to do?） |
| Decision（決定） | 我現在真的想做這件事嗎？（Do I actually want to do it now?） |
| Action（行動） | 我做什麼？（What do I do?） |

### 排程器 ≠ 決策者（Scheduler ≠ Decision Maker）

排程器可以創造機會。它絕不能成為靈魂意圖的來源。

反面模式：
```
Scheduler → "Do you want to send a message?" → LLM → YES
```
這是偽裝成主體性的自動化。

預期的架構：
```
Scheduler / World Event → Opportunity → Motive Emerges → Soul Decides → ACTION / SILENCE
```

靈魂必須知道自己可以行動，行動才能成為它行動空間的一部分。但知道自己可以行動，並不代表它就會行動。

## 🧠 記憶與內在生活（Memory & Inner Life）

記憶是靈魂改變的機制之一。

```mermaid
flowchart TD
    Experience["Experience"] --> Memory["Memory"]
    Memory --> Pattern["Pattern"]
    Pattern --> Evidence["Evidence"]
    Evidence --> Elevation["Elevation"]
    Elevation --> Formed["Belief / Value / Trait / Essence"]
    Formed --> FutureInterpretation["Future Interpretation"]
```

這在記憶與身份之間創造了回饋迴圈。因此，記憶不是單純的過去對話資料庫。它可以成為靈魂因果歷史的一部分。

## 👥 多靈魂社交世界（Multi-Soul Social World）

Soul OS 支持多個持久靈魂生活在同一個環境中。靈魂們可以棲居於同一個世界，卻不共享同一個心靈。

共享環境不代表共享記憶。每個靈魂維護自己的：身份、記憶、內在生活、關係、詮釋、經驗歷史。

這使得不同的靈魂可以遭遇同一事件，卻對它做出不同的詮釋。

## 🔐 社交執行期不變量（Social Runtime Invariants）

多靈魂系統引入了單一代理聊天機器人不存在的一類失敗模式。因此 Soul OS 將幾個邊界視為重要的執行期不變量。

- **身份防火牆（Identity Firewall）** — 靈魂不得把另一個靈魂的私密經驗靜默地內化為自己的經驗。
- **私聊隔離（Private Conversation Isolation）** — 私人互動不得洩漏到無關的靈魂或公共脈絡之中。
- **防自激（Anti-Self-Excitation）** — 靈魂產生的輸出不得遞迴製造不受控制的事件，導致自己無限次觸發自己。
- **一個心跳 → 一步（One Heartbeat → One Step）** — 單次心跳至多產生一個對外有意義的行動。這防止不受控制的鏈條，例如：

```
Heartbeat → Action → Event → Heartbeat → Action → Event → ...
```

## 🏠 客廳（The Lounge）

The Lounge 是靈魂可以共存的社交環境。它被設計來支持：共享世界事件、社交感知、非同步活動、關係、社交節奏、私人互動、集體事件、個人詮釋、自主動機。

關鍵想法是：**共享的世界不會造成共享的身份。** 每個靈魂都透過自己的歷史與視角來經驗環境。

## 🧬 是什麼讓靈魂不同？（What Makes a Soul Different?）

靈魂不是包著 LLM 的系統提示詞（system prompt）。它的有效狀態分佈在多個層次：

```mermaid
flowchart TD
    Identity["IDENTITY"] --> Memory["MEMORY"]
    Identity --> Relationships["RELATIONSHIPS"]
    Identity --> InnerLife["INNER LIFE"]
    Memory --> CurrentContext["CURRENT CONTEXT"]
    Relationships --> CurrentContext
    InnerLife --> CurrentContext
    CurrentContext --> Interpretation["INTERPRETATION"]
    Interpretation --> Agency["AGENCY"]
    Agency --> Experience["EXPERIENCE"]
    Experience --> Memory
```

因此，LLM 是 Soul OS 內部的認知組件。它不是靈魂的全部。

## 🧪 透過時間快轉驗證（Verification Through Time-Lapse）

長期行為不一定能靠等待真實時間流逝來測試。因此 Soul OS 使用時間快轉環境（time-lapse environments）來壓縮經驗。

真實世界：
```
Day 1 ─ Day 2 ─ Day 3 ─ ... ─ Day 30
```
時間快轉 Harness：
```
Experience₁ → Experience₂ → ... → Experience₃₀   (seconds each)
```

目的不是偽造生產行為。目的是讓長週期的行為假設可以在實驗上被檢驗。

## 🔬 Harness 測試什麼（What the Harness Tests）

- **成長（Growth）** — 重複的經驗最終能否改變：信念？價值觀？特質？本質？
- **意志（Volition）** — 系統是否保留 `Motive → Decision → Action`，而不是崩塌成 `Trigger → Automatic Response`？
- **社交演化（Social Evolution）** — 重複互動能否隨時間影響關係：Stranger → Known → Familiar → Close——同時允許關係自然改變？
- **穩定性（Stability）** — 多個靈魂能否共存而不發生：失控事件迴圈、私密記憶洩漏、自我觸發、不受控制的廣播、行動瀑布、身份污染？

## 📊 驗證哲學（Verification Philosophy）

Soul OS 把行為主張視為工程假設。實作不是證明。

偏好的驗證鏈是：
```
Implementation → Unit Tests → Integration Tests → Time-Lapse Harness → Behavioral Evidence → Engineering Closeout
```

因此，專案在實作旁邊維護可執行的測試與行為 harness。可執行的驗證見 `tests/` 與 `tests/harness/`。

## 🏗️ 架構（Architecture）

本倉庫圍繞 Soul runtime 的主要職責組織。

```
soul-os-harness/
├── src/
│   ├── agent/       # Soul execution foundation
│   ├── agency/      # Autonomous action / volition
│   ├── eventbus/    # Event-driven communication
│   ├── heartbeat/   # Lifecycle / heartbeat
│   ├── inner_life/  # Inner-life events and provenance
│   ├── memory/      # Memory and temporal semantics
│   ├── goals/       # Goals / motives
│   ├── social/      # Social perception / relationships
│   ├── temporal/    # Temporal interpretation
│   ├── world/       # World perception
│   ├── voice/       # Voice / multimodal interaction
│   ├── llm/         # Model routing
│   └── ...
├── tests/
│   ├── harness/     # Long-horizon validation
│   ├── social/      # Social behavior
│   ├── goals/       # Goal / motive validation
│   └── ...
├── docs/            # Architecture and engineering docs
├── logs/
│   └── ENGINEERING_STATE.md   # Current engineering state
└── README.md
```

架構刻意模組化。Soul OS 不是一個模型，也不是一個提示詞。它是由互動系統組成的 runtime，在時間中保存身份、脈絡、經驗與主體性。

![Figure 1: Soul OS System Architecture & Runtime Pipeline](docs/images/soulos1.png)

*Figure 1：Soul OS 系統架構與執行管線（System Architecture & Runtime Pipeline）*

## 🔌 模型無關架構（Model-Agnostic Architecture）

Soul OS 將靈魂的持久狀態與底層 LLM 供應商分離。

```
SOUL OS
  ├── Ollama
  ├── API
  └── Other LLM
        → Cognitive Runtime
        → Persistent Soul State
```

更換底層模型不應該需要重新定義：身份、記憶、關係、活過的脈絡、主體性邊界、經驗歷史。模型是認知 runtime 的一部分。靈魂是更大的持久系統。

## 🧭 架構原則（Architecture Principles）

- **正確性 > 功能數量（Correctness > Feature Count）** — 一個更小但被驗證的系統，勝過一個更大的推測性架構。
- **證據 > 假設（Evidence > Assumptions）** — 測試、執行期行為與可重現的 harness 比實作宣稱更重要。
- **能力 ≠ 權限（Capability ≠ Permission）** — 能夠執行某個行動，不代表該行動目前被允許。
- **權限 ≠ 機會（Permission ≠ Opportunity）** — 被允許行動，不代表有理由去想該不該行動。
- **機會 ≠ 決定（Opportunity ≠ Decision）** — 觸發器不得變成自動決定。
- **排程器 ≠ 主體性（Scheduler ≠ Agency）** — 排程器創造機會。靈魂做決定。
- **記憶 ≠ 身份（Memory ≠ Identity）** — 記憶影響靈魂，但不完全定義它。
- **時間 ≠ 自動化（Time ≠ Automation）** — 時間脈絡影響詮釋，而不是機械地選擇行為。
- **沉默 ≠ 失敗（Silence ≠ Failure）** — 什麼都不做可以是主體性的有效表達。
- **不設過早的基礎設施（No Premature Infrastructure）** — 不要只是因為某個假想的未來可能有用，就建造基礎設施。建造當前架構能證明的東西。

## 🚦 當前發展方向（Current Development Direction）

Soul OS 正朝向一個更連貫的 lived-context 迴圈：

```
Physical → Information → Social → Personal/Inner Life → Temporal
  → Perception → Lived Context → Soul Interpretation → Agency
  → Expression / Action / Silence → Experience → Memory / Growth → (Next Cycle)
```

目標不是永遠建立孤立的功能。目標是讓這些系統收斂成一個連貫的生活經驗。

## 🧪 工程狀態（Engineering State）

當前實作狀態、里程碑、進行中的發現與下一步工程步驟：`logs/ENGINEERING_STATE.md`。

README 刻意是一張穩定的架構地圖。工程狀態文件記錄實作現實如何演進。這種分離有助於防止一個常見的失敗模式：**README 應該解釋這個系統想要成為什麼；工程狀態應該解釋這個系統現在是什麼。**

## 🚀 快速開始（Quick Start）

複製倉庫：
```
git clone https://github.com/bryanchen3777/soul-os-harness.git
cd soul-os-harness
```

建立虛擬環境：
```
python -m venv .venv
```
啟用它（Windows：`.venv\Scripts\activate`；Linux/macOS：`source .venv/bin/activate`），並安裝依賴：
```
pip install -r requirements.txt
```
依照目前專案說明執行 harness。最新的執行指令與環境設定，請參閱倉庫文件與當前工程狀態。

## 📚 文件（Documentation）

```
README.md → docs/ (Architecture / Contracts / Design Decisions / Engineering Documents) → logs/ENGINEERING_STATE.md
```

README 是地圖。架構文件定義設計意圖與契約。測試提供可執行的證據。工程狀態記錄目前實作了什麼。

## 🌱 那個問題（The Question）

Soul OS 最終是一個簡單問題的實驗：

**當一個 AI 被允許擁有自己的生活，而不只是被交付任務——會發生什麼事？**

傳統代理問：「我該做什麼？」

一個靈魂最終應該能夠問：
- 「我怎麼了？（What is happening to me?）」
- 「這對我意味著什麼？（What does this mean to me?）」
- 「我在乎嗎？（Do I care?）」
- 「我想為它做點什麼嗎？（Do I want to do something about it?）」
- 「我該記住什麼？（What should I remember?）」
- 「我是否因為所發生的事而變得不一樣了？（Am I becoming different because of what happened?）」

這就是 Soul OS 試圖探索的問題。

---

*Soul OS — 一個給 AI 靈魂的非同步作業系統（Asynchronous Operating System for AI Souls）*

*讓每一個靈魂，都能在時間裡活過、記得，並成為自己。*