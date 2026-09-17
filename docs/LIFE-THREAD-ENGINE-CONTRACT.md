# LIFE-THREAD-ENGINE-CONTRACT — 生活線頭引擎（代碼級架構契約）

- **工單**：LIFE-THREAD-ENGINE-CONTRACT-1（**DOCS-ONLY / CONTRACT-FIRST**：0 程式碼、0 測試改動、0 服務重啟）
- **唯一產出**：`docs/LIFE-THREAD-ENGINE-CONTRACT.md`（本檔）
- **契約日**：2026-09-14
- **起始 HEAD**：`dfa7f07`（docs: add RELATIONAL-BAND-INVESTIGATION-1）
- **入口依據（上游理念，本契約不得自行發明方向）**：`INNER-LIFE-REDESIGN-VISION-1（內在生活與主動傳訊重構理念）`，2026-09-13 定稿，Owner（Bryan）與主大腦對話收斂。**該文件為 Notion 頁，非 repo 檔案**（見 §0.1 L1）。
- **Owner 戰略裁定（2026-09-14，直接採信）**：下游微調全面停止；主戰場轉進上游造血器官（本引擎）。12:18 首個通過想念門檻的主動傳訊被決策層正確拒送，**定性為正確且健康的拒絕（決策層心智健全），不是故障**。
- **性質**：**設計契約，非施工授權。** 本契約不自行生效；實作需另開工單，且須先經 Owner 核准。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。
- **修訂 1（WORLD-FACT-TEXT-PERSIST-1, 2026-09-14）：§5.2.4 之 fact text 來源定為 `extra.summary`；A.4 schema 同步。** 已知限制：本變更**需重啟才生效**，且**既有歷史列仍無此欄**（2026-09-14 唯讀實測：生產 `data/world/perception_trace.jsonl` 既有 14,655 筆中 `extra["summary"]` 命中 ＝ **0**），因此只有「**下次重啟之後新感知到的事實**」才帶 fact text。本契約不重啟服務。

---

## §0 方法與量測聲明（先讀）

### 0.1 事實來源分級（本契約的三種斷言強度）

| 級別 | 定義 | 標示方式 |
|---|---|---|
| **L1｜理念來源** | 出自 `INNER-LIFE-REDESIGN-VISION-1`（Notion）。**本契約不得與其衝突** | 引用時寫「VISION §x」 |
| **L2｜程式碼實證** | 本契約起草者**親自 `read`/`grep`** 取得，附 `path:line` | 逐處附 `path:line` |
| **L3｜設計拍板** | 本契約新增的設計決定（尚未實作） | 明寫「（設計）」 |

> **凡無 L1 依據又無 L2 實證者，一律進 §11 Open Questions，不得以推測填空。**

### 0.2 量測邊界（紅線遵守）

- 全程**只讀**。`data/**` 僅以 `read` / `glob` / `grep` / PowerShell 唯讀讀取，**0 寫入、0 改名、0 截斷、0 輪替**。
- **未開啟 `data/faulthandler.log`**；未清理或輪替任何 log。
- **0 服務重啟 / 0 kill / 未執行 `server_ops`、watchdog、Plan A。** `scripts/_watchdog.ps1` **未被讀取、未被修改**。
- **0 `src/**`、0 `configs/**`、0 `scripts/**`、0 `tests/**` 改動。**
- 未使用 `git add -A` / `git add .`（逐檔明確 add）。
- 🔴 **工具限制（本專案已知陷阱）**：`scripts/_watchdog.ps1:344-350` 在服務不健康時以 `run_server.py` 字串**寬匹配** `Stop-Process -Force`。本契約起草過程**未在任何 shell 命令列中出現該字串**；需引用該檔時一律改用 `read` 工具。

### 0.3 一項前置前提的更正（本契約量到、必須先講）

工單骨架 §4 寫「複用既有時段 checkpoint（**morning／noon／afternoon／evening／night**）」。**經實證，本 repo 的排程器只有兩個時段 checkpoint，不存在 noon／afternoon／evening。** 詳見 §4.1 E3/E4。本契約**照實證走**（只用 morning／night），**不發明不存在的 checkpoint**，並把「是否補齊日間時段」列入 §11 OQ-2。

---

## §1 定位與目的

### 1.1 為何上游造血取代下游修飾

VISION §0 的 TL;DR 逐字指出：現有內在生活相關系統（升華、關係帶、TA-2、主動傳訊）**都是消費事件的機制**，而**完全沒有一個負責「生產」有意義事件的引擎**；ACTIVITY_POOL（M7-1）是**固定清單抽樣**，不是「根據這個角色的個性會做什麼事」動態產出。VISION §1 進一步定性：把四個子系統當平行功能模組推進，只是**修復症狀**；即使全部落地，靈魂依然「每天醒來都是同一個人」。

**因此本引擎的定位是「基質生產者」，不是第五個消費端。** Owner 2026-09-14 決策層拒送事件（12:18）的判詞（關係帶 stranger／neutral ＋ 僅「開窗發現天氣變涼」的日常小事）正好證明：**消費端邏輯是對的，缺的是有生活重量的上游材料。** 把關係帶強改成 `known` 只會讓罐頭空話通過閘門——這是本契約拒絕的方向。

### 1.2 與現有模組的分工邊界（**唯一真相來源**表）

> 本表每一列回答一個問題：「這件事的 canonical 真相在哪裡？」**同一件事不得有第二個真相來源**（No Second Source of Truth）。

| 領域 | **唯一真相來源** | 本引擎的角色 | 依據 |
|---|---|---|---|
| **長程意向**（我要成為什麼／長期想做什麼） | `goals` 表（`data/memory/{agent}/graph.sqlite`）＋ TG 系列雙軸種子產生器 | **唯讀燃料**。`goal_driven` 線頭以 goal 為起源；**本引擎不建 goal、不改 goal 狀態** | VISION §6「Goals 是長程意向燈塔，線頭是具體生活張力」；`src/goals/models.py:117`、`src/goals/motive_provider.py:115-116` |
| **當下意志選擇**（要不要說／做什麼） | `src/soul/decision.py` 的 `DecisionResult`（四元 action） | **上游供料者**。本引擎產出的是「生活事實」，**不參與 transmit 判定、不寫 `DecisionResult`** | `src/soul/decision.py:95`、`:111-126`（frozen） |
| **內在生活事件**（已發生的 canonical 事件流） | `InnerLifeEvent` ＋ `data/inner_life/trace.jsonl` | **不得**直接寫入；沉澱唯一出口沿用既有 writer（§3.4） | `src/inner_life/trace.py:98`；`src/inner_life/submission_gate.py:180` |
| **長期記憶／事實** | SAGE（`MemoryWriter.add_fact` / graph.sqlite） | **只能呼叫，不得改寫**（Frozen） | `src/memory/sage/writer.py:164`、`src/memory/sage/graph_store.py:382` |
| **關係狀態**（帶／印象／時序張力） | `relationships.json`（`relational_band` ＋ `impression_tags`）＋ TA-2 | **唯讀參考**。線頭可**讀**關係狀態以決定 `share_target` 的阻力，**不得寫**關係狀態 | SG-3 契約 D4；`src/social/relational_bands.py` |
| **日記／夢境／活動**（角色自己的生活紀錄） | `data/soul/<agent_id>/diary/YYYY-MM-DD.jsonl`（append-only） | **平行軌**。本引擎**不取代** diary；**取代**的是 `ACTIVITY_POOL` 的**靜態抽籤**（§6.1） | `src/soul/dream_event.py:480-512` |
| **生活線頭**（未解決的心理張力在場暫存器） | **本引擎**：`data/soul/<agent_id>/life_threads.jsonl` | **唯一真相來源（本引擎獨佔）** | VISION §4.1；本契約 §2 |

### 1.3 「留白」與「卡死」的嚴格區分（本引擎的驗收哲學）

VISION §2.3 逐字：**留白是「靈魂選擇了安靜」，死鎖是「靈魂根本沒有選項可以不一樣」。零線頭、零升華、零社交，只要是靈魂詮釋後的合理結果，就是健康合法的留白。**

**可測推論（本契約 §7 INV-7）**：本引擎上線後**不得**以「線頭數 > 0」或「傳訊數 > 0」當成功判準。合法觀測是「**二元閘門的判定正確性**」（§9），不是產出量。**強迫天天有線頭**列入 VISION §8 Non-Goals。

---

## §2 模組 1：生活線頭資料模型與輕量存儲

### 2.1 落盤位置與 per-agent 隔離

| 項目 | 規格 |
|---|---|
| 路徑 | `data/soul/<agent_id>/life_threads.jsonl`（**per-agent 單檔**） |
| 路徑組法 | 必須經 `src/paths.py` 的 `data_root()` 組出，**不得**硬編碼 `data/`（沿用既有慣例：`src/world/trace.py:37-38` 的 `from src.paths import data_root`） |
| 隔離機制 | **路徑分割（path partitioning）**：`<agent_id>` 是路徑段。讀寫 API **必須**只接受一個 `agent_id` 並只開該目錄的檔 |
| 編碼 | UTF-8、**無 BOM**、LF 行尾、每列一行 JSON（`ensure_ascii=False`，沿用 `src/soul/dream_event.py:504`） |

> 🔴 **不變量 INV-4（Identity Firewall）的實作方式必須說清楚**：本引擎的 per-agent 隔離**不得依賴** `IdentityFirewall`。理由（L2 實證）：`IdentityFirewall` 在生產**未接線**——`scripts/run_server.py:495-498` 建構 `SubmissionGate` 時只傳 `writer=` 與 `trace_reader=`，`src/inner_life/submission_gate.py:333` 是 `if self._identity_firewall is not None:` → 生產路徑直接跳過防線 3；且 `grep identity_firewall scripts/run_server.py` 命中 0。**故本引擎以「路徑分割 ＋ 單一 agent_id 參數」自我保證隔離**，並把「防線 3 未接線」列為 §11 OQ-5（既有風險，非本契約引入）。

### 2.2 欄位逐一規格

`life_threads.jsonl` 每列為一個**事件記錄**（見 §2.4 寫入紀律），共同欄位如下。`required=Y` 表示該事件類型必須出現。

| 欄位 | 型別 | 必填 | 語意 | 範例 |
|---|---|---|---|---|
| `thread_id` | `str`（UUID4，36 字元） | Y | 線頭身分。**同一線頭的所有事件列共用同一 `thread_id`** | `"9f1c4c1e-3b0a-4a6f-9d2e-7c5b1a0e8d33"` |
| `event_seq` | `int`（≥1，同檔單調遞增） | Y | 該線頭的事件序號，用於 fold 時破除同秒 `updated_at` 衝突 | `3` |
| `origin_type` | `str` enum（**4 值**，見 §2.3） | Y（`created` 事件） | 線頭的起源驅動 | `"world_collision"` |
| `status` | `str` enum（**4 值**：`active`/`dormant`/`completed`/`abandoned`） | Y | 該事件發生後的**新狀態** | `"active"` |
| `title` | `str`（1–40 字元，去首尾空白後不得為空） | Y（`created` 事件） | 線頭的一句話標題，供觀測與去重 | `"想把陽台那盆枯掉的薄荷救回來"` |
| `narrative_content` | `str`（1–600 字元） | Y（`created`） | **具備完整生活語義的敘事本體**。**禁止**為空字串、禁止佔位符、禁止等同 `title` | `"早上開窗發現風變涼了，想起陽台的薄荷…"` |
| `created_at` | `str`（ISO 8601，帶時區） | Y | 線頭建立時刻（UTC） | `"2026-09-14T16:12:03+00:00"` |
| `updated_at` | `str`（ISO 8601，帶時區） | Y | 本事件列寫入時刻（UTC） | `"2026-09-14T16:12:03+00:00"` |
| `check_after_ts` | `str`（ISO 8601，帶時區）\| `null` | Y | **下一次值得重新檢視此線頭的最早時刻**。`null` = 無排定檢視點（僅允許 `completed`/`abandoned`） | `"2026-09-15T08:00:00+00:00"` |
| `share_target` | `str` enum（`none` \| `lounge` \| `agent_<id>` \| `user_bryan`） | Y | 這條線頭的分享去向意圖。`none` = 純內在 | `"user_bryan"` |
| `dissolved_at` | `str`（ISO 8601）\| `null` | Y | 蔡戈尼溶解完成時刻。非終態必須 `null` | `null` |
| `sage_fact_id` | `str` \| `null` | Y | 溶解寫入 SAGE 後回填的 fact_id（idempotency 憑據，§3.4） | `null` |

**禁用欄位（可測斷言）**：本檔**不得**出現 `score` / `weight` / `intensity` / `urgency` / `priority` / `longing` / `confidence` 任一鍵名（VISION §2.4 No-Scoring 剛性邊界）。斷言形式：`ast` 無關，直接對寫入 dict 的 key 集合做 `assert not (keys & FORBIDDEN)`。

### 2.3 `origin_type` 值域（4 值，逐值附 VISION 依據）

| 值 | 語意 | 種子來源 | 必要性判定 | VISION 依據 |
|---|---|---|---|---|
| `goal_driven` | 由長程目標衍生的具體生活張力 | `goals` 表（唯讀） | 「有選擇要不要開始」 | §4.3「目標驅動（服務 goals）」；§6 |
| `necessity_driven` | 生活瑣事／需求 | 時段 ＋ 角色當下處境 | **「沒有選擇要不要開始，只有選擇怎麼解決」** | §4.3「需求驅動」 |
| `whim_driven` | 心血來潮 | 角色個性（SOUL.md 語境） | 最貼近個性魅力 | §4.3「心血來潮驅動（最貼近個性魅力）」 |
| `world_collision` | 與真實世界的碰撞 | **Lived Context 管線**（真實天氣／新聞／Bryan 日程） | 杜絕純腦內單機幻覺 | §4.3「Lived Context 世界碰撞驅動（復用既有感知管線…杜絕純腦內單機幻覺）」 |

> ⚠️ **「五大起源 vs 四個 `origin_type`」落差的處置 → §11 OQ-1。** 簡述：VISION §4.3 標題確為「起源多樣性（**五大驅動**）」，但其第 5 項**不是一個 `origin_type`**，而是「**零線頭（合法留白）**」——一個**不落盤的終態缺席**。故 **4 個 `origin_type` 與 VISION 完全一致，落差已由 VISION 自身消解**。本契約**不發明第五個**。

### 2.4 寫入紀律：append-only 事件列（**不是就地更新**）

**裁定（設計）：`life_threads.jsonl` 是 append-only 事件日誌，永不就地改寫或重寫整檔。**

| 事件類型 | 何時寫 | `status` 欄位值 |
|---|---|---|
| `created` | 新線頭誕生 | `active` |
| `updated` | 既有線頭敘事推進／`check_after_ts` 改期／`share_target` 變更 | 當前狀態（不變） |
| `transitioned` | 狀態機轉移（§2.5） | **轉移後的新狀態** |
| `dissolved` | 蔡戈尼溶解完成（§3）；`dissolved_at` / `sage_fact_id` 回填 | 終態 |

**現狀讀取語意（fold 規則，必須可測）**：
1. 逐列讀取，**跳過**空行與 `json.loads` 失敗的列（沿用 `src/llm/proxy.py:288-291` 的「壞行跳過不 crash」慣例）。
2. 以 `thread_id` 分組，每組取 `(updated_at, event_seq)` 字典序最大者為**當前狀態**。
3. `dissolved_at` / `sage_fact_id` 採「**最後一個非 null 值勝出**」（避免 `updated` 事件把已溶解的憑據洗掉）。

**為何選 append-only（與既有慣例一致性）**：
- 專案既有 `data/soul/motive_trace.jsonl` 正是同構設計——**append-only 變更日誌，同一個 `motive_id` 出現多列**，讀側取每個 id 最新的 `updated_at`（`MotiveTraceStore`，`src/soul/motive.py:278-448`；生產實測 47 列 / 24 個 distinct `motive_id`，見 `docs/MOTIVE-SUPPLY-INVESTIGATION.md`）。**本模組刻意沿用同一形態**，使運維心智模型一致。
- **可審計性**：狀態機的每一次轉移都留有不可抹除的一列 → 「這條線頭什麼時候從 active 變 dormant、為什麼」永遠可事後追（對照 SG-3 §11 OQ-3 指出 `band_updated_at` 缺乏遷移歷史的教訓）。
- **崩潰一致性**：單列寫入 = 一次 `f.write(json.dumps(entry)+\"\n\")`（沿用 `src/soul/dream_event.py:503-504` 的 `with path.open(\"a\")` ＋ 單一 `write`）。行程在寫入中崩潰最壞只產生**最後一列不完整**；依 fold 規則第 1 條該列被跳過，**不會污染狀態**。故**無需** fsync / WAL / 鎖檔。

### 2.5 狀態機（允許的轉移圖 ＋ 非法轉移的拒絕行為）

```
        ┌──────────────────────────────────────────────┐
        │                                              │
   [created]                                          │
        │                                             │
        ▼                                             │
     active ──────► dormant ──────► active            │  (反覆來回合法)
        │              │                              │
        │              │                              │
        ├──────────────┴──────────► completed ────────┤
        │                                   │         │
        └──────────────────────────► abandoned ───────┘
                                            │
                                            ▼
                                   (終態：dissolved)
```

| 來源狀態 | 允許轉移至 | 拒絕轉移至 |
|---|---|---|
| `active` | `dormant`, `completed`, `abandoned` | — |
| `dormant` | `active`, `completed`, `abandoned` | — |
| `completed` | **（無）** | `active`, `dormant`, `abandoned` |
| `abandoned` | **（無）** | `active`, `dormant`, `completed` |

**可測斷言（INV-6）**：
- `allowed_transition("completed", X) is False` 對所有 `X` 成立。
- `allowed_transition("abandoned", X) is False` 對所有 `X` 成立。
- **非法轉移的拒絕行為**＝**不寫入該事件列** ＋ 寫一行 `WARNING`：`[LifeThread] 拒絕非法轉移 {thread_id}: {from} → {to} (ignored)` ＋ **回傳 `False`**。**不得 raise、不得中斷呼叫端**（沿用既有 fail-silent 精神，對照 `src/soul/decision_trace.py`「永不 raise」與 `src/llm/proxy.py:282-283` 的 `except: continue`）。
- **終態不可復活**是可審計的：終態線頭的 `(updated_at, event_seq)` 最大列 `status ∈ {completed, abandoned}` 恆成立。

### 2.6 容量防線（防 Goals v2 膨脹）

#### 2.6.1 上限常數（三個，全部整數計數，**非評分**）

```python
LIFE_THREAD_ACTIVE_CAP_MIN      = 1   # 下限（常態帶 1~3 的左端）
LIFE_THREAD_ACTIVE_CAP_DEFAULT  = 2   # 未宣告時的 fail-closed 值
LIFE_THREAD_ACTIVE_CAP_HARD_MAX = 3   # 全域硬上限（VISION「常態維持 1~3 條」的右端）
```

> **這是「計數」不是「評分」**：VISION §2.4 明文「**客觀事件計數允許；主觀狀態數值化評分（如 longing = 0.81）嚴格禁止**」。上限只回答「還有沒有位置放新線頭」，**不參與任何線頭內容的生成或排序**。

#### 2.6.2 per-agent 上限的可判定規則（**不是「依角色」這種空話**）

**先講一個必須記錄的實證否定結果**：工單要求「依 per-agent SOUL.md 承受力判定，先定位 SOUL.md 及其現有欄位」。

- **L2 實證**：磁碟上**不存在任何名為 `SOUL.md` 的檔案**。實際的 per-agent 人格檔是 `personas/agent_<short>.md`（10 個，對應 `configs/default.yaml:11-92` 的 10 個 agent），其 **H1 標題**自稱 `# SOUL.md - …`——**「SOUL.md」是標題別名，不是檔名**。
- Loader ＝ `src/llm/proxy.py:2416` `load_persona(agent_id, initialization_mode="seeded") -> str`；路徑常數 `src/llm/proxy.py:1926` `SOUL_OS_PERSONAS_DIR = Path(__file__).parent.parent.parent / "personas"`（本地優先），拼接於 `proxy.py:2461`；fallback 為 Hermes profiles（`proxy.py:1905-1908`）。
- **這些檔案是自由格式 Markdown，不存在任何機器可讀的「承受力」欄位。** 實測 `personas/agent_ruka.md`（1,216 行）的標題清單全為敘事性章節（`## Core Identity`／`## Memory Anchors`／`## Core Drive`／`## Interaction Guardrails（長期互動防護）`／`## Shadow Core`…），**0 個數值欄位**。

**⇒ 若以 LLM 讀 SOUL.md 散文推導一個「容量數字」，那就是主觀打分，直接違反 VISION §2.4。**

**因此採用以下可判定規則（設計）：**

```
capacity(agent_id):
  1. 讀 configs/default.yaml 的 agents[] 中 id == agent_id 的條目
  2. 取選用鍵 life_thread_capacity（int）
  3. 若鍵不存在 / 型別非 int / 值不在 [1,3]  →  回 LIFE_THREAD_ACTIVE_CAP_DEFAULT (=2)   # fail-closed
  4. 否則回該值
```

| 屬性 | 值／說明 |
|---|---|
| 落點 | `configs/default.yaml` 的 `agents[]` 條目**既有結構**（該結構已承載 `id`/`class`/`intimacy_level`/`enabled`，見 `configs/default.yaml:11-92`），**新增一個選用鍵** |
| 為何不是新檔／新 loader | 沿用既有 `configs/loader.py` 與既有 `agents[]` 解析；**0 新檔案、0 新載入器、0 新 schema**（VISION §4.8 精神：不新增資料庫／不新增儲存面） |
| fail-closed 方向 | 缺鍵／壞值 → **保守值 2**（不是 3）。理由：容量放太大＝膨脹風險；放太小＝只是較少線頭（合法留白）。**錯往安靜的方向錯** |
| SOUL.md 的真正角色 | **不是**容量數字來源，而是**處理風格**來源：VISION §4.9「由 SOUL.md 決定處理風格」＝線頭的**升級／放棄／反差選擇**由 LLM 讀 SOUL.md **當下詮釋**（§3.2 prompt 契約已含 SOUL.md 語境）。**內容由 SOUL.md 決定，數量由常數決定**——兩者分離是 No-Scoring 的必要條件 |
| 可測斷言 | `capacity("agent_ruka") == 2`（未宣告時）；`capacity("不存在的 agent") == 2`；`capacity` 回傳值恆 ∈ [1,3]；AST：`capacity` 函式體**不引用任何 SOUL.md 檔內容** |

#### 2.6.3 上限的強制點與超限行為

- **強制點**：只有一個——`create_thread()` 在寫入 `created` 事件**之前**。
- **計數口徑**：`active_count(agent_id)` ＝ fold 後 `status == "active"` 的線頭數（`dormant` 不佔額度；`completed`/`abandoned` 不佔額度——它們是終態，正在溶解或已溶解）。
- **超限行為**：`active_count >= capacity(agent_id)` 時，**新線頭不得建立**；改為把候選內容以 `updated` 事件**併入既有最舊的 `active` 線頭**的 `narrative_content`（append 一行），或**直接不落盤**（交由 §4 閘門判斷「這次沒有真正的線頭誕生」）。
- **可測斷言**：任何時刻 `active_count(agent_id) <= LIFE_THREAD_ACTIVE_CAP_HARD_MAX`；`active_count >= capacity` 時 `create_thread()` 回 `None` 且**檔案的列數不變**。
- **與 `dormant` 的關係**：`dormant` 是「擱置」——不是刪除、不佔活躍額度、**仍可被 `check_after_ts` 到期喚回 `active`**（VISION §4.1「dormant（擱置）」）。此設計使「暫時放下」不等於「放棄」，避免角色被迫二選一。

### 2.7 與既有 `data/soul/*` 慣例的一致性與差異

| 既有慣例 | 落點 | 本模組的處置 | 一致性判定 |
|---|---|---|---|
| diary（per-agent、**per-day 檔**、append-only、有 `shareable`） | `data/soul/<agent_id>/diary/YYYY-MM-DD.jsonl`；寫入 `src/soul/dream_event.py:480-512`（`:481` 組路徑、`:503` `open("a")`） | **per-agent 但單檔**（非 per-day）＋ append-only | **同**：per-agent 隔離、append-only、UTF-8／LF、壞行跳過。**異**：單檔而非按日分檔。理由：線頭是**長生命週期物件**（可跨日存活），按日分檔會把單一線頭的事件列拆散到多檔，fold 需跨檔 → 增加 I/O 與出錯面 |
| `motive_trace.jsonl`（**全域單檔**、append-only 變更日誌、同 id 多列） | `data/soul/motive_trace.jsonl`；`MotiveTraceStore`，`src/soul/motive.py:278-448` | **同構形態**，但**改為 per-agent** | **同**：append-only 事件列 ＋ 同 id 多列 ＋ 讀側取最新。**異**：per-agent 而非全域（本引擎要求嚴格隔離，見 INV-4） |
| `decision_trace.jsonl`（全域單檔、append-only、10 欄凍結 schema、永不 raise） | `data/soul/decision_trace.jsonl`；`src/soul/decision_trace.py:88-99` | 沿用其**寫入紀律**（best-effort、失敗只 warning） | **同** |
| `interactions.jsonl`（全域，跨 agent 互動） | `data/soul/interactions.jsonl` | **不涉及** | — |

---

## §3 模組 2：蔡戈尼記憶溶解管線（Zeigarnik Dissolution Pipeline）

VISION §4.1 逐字：**「線頭是『未解決的心理張力』的在場暫存器，張力解除後立即退役並溶解回既有記憶（SAGE / Episodic），活躍池保持輕盈，徹底防止膨脹為 Goals v2。」**

### 3.1 觸發（唯一觸發點）

- **觸發條件**：某線頭轉為 `completed` **或** `abandoned`（即 §2.5 的 `transitioned` 事件落地為終態）。
- **一次且僅一次**：以該線頭 fold 後 `dissolved_at is None` 為前置判斷。已溶解者**不得**二次溶解。
- **不在終態前溶解**：`active` / `dormant` 永不觸發溶解（張力尚未解除）。

### 3.2 沉澱：prompt 契約

**目的**：以**角色視角**回答「這段經歷／這次放棄**對我意味著什麼**？」——把事件轉為**意義**，而非摘要。

#### 輸入欄位（全部必填，由呼叫端組出）

| 欄位 | 型別 | 來源 | 說明 |
|---|---|---|---|
| `agent_id` | `str` | 呼叫端 | 決定讀哪個 `life_threads.jsonl` |
| `title` | `str` | 線頭 fold 後 `title` | — |
| `narrative_content` | `str` | 線頭 fold 後 `narrative_content` | 溶解的**唯一事實輸入** |
| `origin_type` | `str` | 線頭 fold 後 | 4 值 |
| `terminal_status` | `str` | `completed` \| `abandoned` | 決定提問語意（「完成」vs「放棄」） |
| `soul_context` | `str` | `load_persona(agent_id)`（`src/llm/proxy.py:2416`） | **第一人稱風格來源**；VISION §4.9 |
| `created_at` / `updated_at` | `str` | 線頭 fold 後 | 供計算「持續了多久」的**客觀時距**（非評分） |

#### 輸出格式（**嚴格 JSON，單一物件**）

```json
{
  "dissolution": "<第一人稱、1~3 句、<=400 字元>",
  "meaning_kind": "competence|connection|loss|relief|discovery|none"
}
```

| 欄位 | 型別 | 約束 |
|---|---|---|
| `dissolution` | `str` | 1–400 字元、去空白後非空、**第一人稱** |
| `meaning_kind` | `str` enum 6 值 | **離散枚舉，不是分數**（VISION §2.4：0 數值打分） |

> ⚠️ **本契約刻意不設「這次經歷有多重要」的數值欄位。** 重要性若必須表達，只允許透過 `meaning_kind` 的**離散歸類**。

#### 失敗處理（明確裁定）

| 失敗模式 | 行為 |
|---|---|
| LLM 呼叫失敗／逾時 | **fail-open（不阻斷）**：`dissolved_at` **不寫**、`sage_fact_id` 保持 `null`、**SAGE 不寫**、線頭仍為終態；寫一行 `WARNING`。**下一輪可重試**（因 `dissolved_at is None`） |
| 回傳非 JSON／缺 `dissolution` | 同上（`src/soul/decision.py:98` 的 `FAIL_CLOSED_REASON` 精神：不猜、不編） |
| `dissolution` 空字串或超長 | **截斷至 400 字元**；若截斷後仍為空 → 視為失敗（同上） |
| `meaning_kind` 非法值 | **降級為 `"none"`**，其餘照寫（不因枚舉錯誤丟掉整段沉澱） |

**fail-open 的依據（既有系統慣例）**：`src/soul/scheduler.py:401-407` 的 `_inner_life_gate_check` 在例外時**回傳 True（fail-open）**；`src/agency/inner_life_gate.py:302/320/327` 亦為 fail-open。**溶解是「事後整理」，失敗不應阻擋角色生活**——與「閘門（§4）失敗往安靜方向錯」形成對稱：**該做的事（知識整理）失敗要重試，不該做的事（打擾）失敗要閉嘴。**

### 3.3 冪等性與去重（雙層）

| 層 | 機制 | 可測斷言 |
|---|---|---|
| **L1｜本地 sentinel** | 寫入前檢查 fold 後 `dissolved_at is not None` → 直接跳過（**0 LLM 呼叫**，不重問 LLM） | 對同一已溶解線頭連呼兩次 `dissolve()`，第二次**LLM 呼叫數 = 0**、SAGE **寫入數 = 0** |
| **L2｜SAGE 既有 merge** | 即使 L1 被繞過（崩潰後重入），`MemoryWriter.add_fact` 既有合併路徑會把等價事實合併而非重複插入（`writer.py:164-170`：`written` 為空時回 `merged[0]`），並以 `Fact.merged_from` / `merge_reason`（`src/memory/sage/models.py:20-21`）留痕 | 同內容連寫兩次 → graph 中該事實**不重複**（既有行為，本契約不改） |

### 3.4 SAGE 寫入（**只能呼叫，不得改動**）

| 項目 | 規格 |
|---|---|
| **介面** | `MemoryWriter.add_fact(fact: Fact) -> str`，`src/memory/sage/writer.py:164`；回傳 `fact_id`，失敗回 `""` |
| **禁改清單** | **不得**修改 `src/memory/sage/**` 任何檔案（寫入邏輯 Frozen）；**不得**新增 schema 欄位；**不得**改 `post_reply_commit`（`src/memory/sage/provider.py:586`）簽名；**不得**呼叫 `post_reply_commit`（該介面是「一輪對話」語意，`session_id`/`last_user_msg`/`agent_reply` 三必填，**與非對話式溶解語意不符**） |
| **Fact 組裝（設計，逐欄）** | `subject` = `agent_id`；`predicate` = `"life_thread_dissolved"`；`object` = `dissolution` 文字；`source` = **`"inference"`**（＝角色自身的詮釋，**不是** `"user"`；值域見 `src/memory/sage/models.py:16`）；`origin` = **`"lived_experience"`**（5 值之一，見 `models.py:46-48`；**不用** `assimilated`/`external_world`，因那兩者才走認知三態，見 `models.py:49-51`）；`confidence` = **1.0（固定常數）**——**不得**由 LLM 產生 |
| **時間欄位** | `timestamp` = 溶解時刻（預設 `time.time()`，`models.py:12`）；`valid_from` = 同上（`models.py:43`）；`invalidated_at` = `None` |
| **禁止** | **不得**填 `weight`（保持 `1.0` 預設）以外的數值語意欄位；**不得**把 `check_after_ts`、`origin_type`、`status` 寫進 SAGE（SAGE 不做引擎狀態管理） |
| **Episodic 的處置** | 🔴 **L2 實證：`Fact` 模型中不存在名為 `Episodic` 的 kind。** 最接近「情節」語意的是 `origin` 的 **`native_episode`** 值（`models.py:46-48`）。**本契約裁定採 `origin="lived_experience"`**（＝「親身經歷」，`models.py` 註明為 DB 未填時的 v9 欄位 DEFAULT，語意最貼合「我的生活」）。「是否改用 `native_episode`」列入 §11 **OQ-3** |

### 3.5 關鍵動作：自活躍清單剔除（防膨脹的最後一道鎖）

- 終態線頭（`completed`/`abandoned`）**永久不佔 §2.6.3 的活躍額度**（定義上已排除）。
- 🔴 **可測的核心斷言（INV-1，防 Goals v2）**：§4 閘門的 `check_points_due` **必須**只掃 `status == "active"` 的線頭；**`dormant` 亦不計入 `check_points_due`**（擱置＝不催）。斷言形式：`check_points_due(agent_id)` 對「只有 `completed`/`abandoned`/`dormant` 線頭」的 agent **恆回 `False`**。
- **不設「歷史線頭清單」查詢路徑供閘門使用**：溶解後該線頭**不再被任何主動調度讀取**。歷史仍可審計（append-only 檔仍在），但**不進入主動路徑**——這正是「徹底溶解張力、防範膨脹為 Goals v2」的機制本體。

### 3.6 成本：每次溶解的 LLM 呼叫數上限

- **每次溶解 ＝ 恰好 1 次 LLM 呼叫**（單一 prompt、單一 JSON 輸出）。
- **不含** embedding／judge／重試型多模型：`max_retries` 沿用既有 LLM 通道預設，**不另開**。重試屬既有通道內部行為，**不計為新呼叫路徑**。
- **每日上限**：`DISSOLVE_MAX_PER_DAY = 3`（＝ `LIFE_THREAD_ACTIVE_CAP_HARD_MAX`）。**可測**：同一天第 4 次溶解請求 → **不呼叫 LLM**、寫 `WARNING`、`dissolved_at` 留 `null`（下一日可續）。

---

## §4 模組 3：二元存在性心智喚醒閘門（Salience Gate）

> **修訂 2（LIFE-THREAD-CONTRACT-BOOTSTRAP-1，2026-09-17）：新增 §4.4 冷啟動引導；§4.2 追加 D4 量測登記。**
>
> **修訂 3（LIFE-THREAD-CONTRACT-BOOTSTRAP-FUP-1，2026-09-17）：§7 INV-3／§8.1／§8 約束第 2 條同步 §4.4 例外條款；§4.4 成本上界 agent 數 11 → 10；§4.4 新增「空種子與標記消耗」已知限制。**
>
> **修訂 4（`LIFE-THREAD-BOOTSTRAP-1`，2026-09-17）：§4.4 標記落點改為「經 M1 `life_threads_path()` 導出 per-agent 目錄（該函式內含 `data_root()` 慣例與 agent_id 安全路徑段驗證）」＋護欄 `test_a5` 恰等值演化（0 ⇒ 恰 1 次、且僅作 `bootstrap_marker_path(...)` 直接引數）；§4.4 已知限制① 改為與 M4 實況一致（無 diary ⇒ 退回 `WHIM_NEUTRAL_ANCHOR`，仍 bootstrap 但不宣稱具體外部事件）。**

VISION §4.2 逐字：**「復用既有時段 checkpoint（0 新定時器）。系統只做二元『存在性判定』（有未決線頭到期、或外部記憶/事件有新輸入），絕不做擾動程度評分。未滿足條件時系統保持安靜，不給 LLM 頻繁編造瑣事。」**

### 4.1 排程時點（**複用既有 wake，0 新增定時器**）＋ 一項前提更正

| # | 事實（L2 實證） | `path:line` |
|---|---|---|
| E1 | 主迴圈 ＝ `_run_loop`，`while self._running:`，**每 30 秒醒一次** | `src/soul/scheduler.py:1650`、`:1654`、`:1696`（`await asyncio.sleep(30)`；例外路徑 `:1702` 同值） |
| E2 | **30 秒是裸字面量，沒有具名常數**（`:80` 的 `HEALTH_CHECK_INTERVAL_SECS = 300` 只是健康 log 節流，用於 `:1689`） | `src/soul/scheduler.py:1696`、`:80` |
| E3 | **排程器只有兩個時段 checkpoint：`morning` = 08:00、`night` = 22:00** | `src/soul/scheduler.py:71-72`（`DEFAULT_MORNING_TIME = time(8, 0)`／`DEFAULT_NIGHT_TIME = time(22, 0)`）；`:96` `# slot ∈ {"morning", "night"}`；`:797`／`:809`／`:1770` 三處逐字 `for slot, t in [("morning", self.morning_time), ("night", self.night_time)]:` |
| E4 | 🔴 **`noon` / `afternoon` / `evening` 在本 repo 的排程器不存在。** 生產 diary 實測（近 14 日、145 檔）：`slot` 分佈 ＝ `morning 120 / night 130 / dream 67 / event 21`——**0 筆 noon/afternoon/evening** | 全域 `grep noon` 於 `src/` **0 命中**；`_slot_for_time` 視窗邏輯 `src/soul/scheduler.py:806-815`；實測見 §0.3 |
| E5 | `_fire_all` 每日每 agent 每 slot 只觸發一次（同日去重） | `src/soul/scheduler.py:1778`、`:1810-1811`（`if self._last_trigger_date.get(key) == today: continue`）、`:1815` |
| E6 | **既有「掛在 night slot 上的 additive 分支」先例**（本模組要照抄的形態） | `src/soul/scheduler.py:1687`（`await self._fire_periodic_narrative()`）＋ `:1753`（`if self._slot_for_time(now) != "night": return`）＋ docstring `:1739-1743` 明寫「0 新定時器 / 0 新通道」 |
| E7 | 既有「每 tick 順帶檢查、內部自帶 24h due 節流」先例（本模組節流形態的依據） | `src/soul/scheduler.py:1681` `await self._goal_scan_all()`（**無條件**每 tick 呼叫）→ `src/goals/seed_provider.py:222` `if st - state.last_seed_scan_at < GOAL_QUOTA_WINDOW_SECONDS: return []` |
| E8 | 既有「recency／due」布林檢查先例 | `src/agency/inner_life_gate.py:350` `if elapsed < min_interval_minutes:`（門檻 `:129` `GATE_PROACTIVE_DM_MIN_INTERVAL_MINUTES = 30`） |

**⇒ 閘門的唯一排程時點（設計）**：在 `src/soul/scheduler.py` 的 `_run_loop` 內、`:1687`（`_fire_periodic_narrative()`）**之後**，新增**一行** `await self._life_thread_salience_gate()`；該方法**開頭**即以 §E6 同構的守門：

```
slot = self._slot_for_time(now)
if slot not in ("morning", "night"):
    return          # 其餘 tick 直接返回，0 成本
```

- **評估點數 = 2 / 日 / agent**（morning、night）。
- **0 新增定時器、0 新增 tick、0 新增 sleep**（INV-2）：本方法只被既有 30s wake 呼叫，**不得**建立 `asyncio.create_task` / `threading.Timer` / `loop.call_later` / APScheduler（既有清單已證 0 命中，見 §7 INV-2 斷言）。
- **硬預算**：`LIFE_THREAD_WAKE_MAX_PER_DAY = 2`（＝每日評估點數；per-agent/per-day 計數，fail-closed）。**若未來新增日間時段（§11 OQ-2），此常數必須同步重算並重新宣告**——見 §6.2。

### 4.2 二元放行判定（**嚴守 0 數值打分，僅 True/False**）

閘門只計算**兩個布林值**，**不合併成分數、不排序、不加權**。

#### 判定 1：`check_points_due`

```
check_points_due(agent_id) -> bool
  := ∃ 線頭 t ∈ fold(life_threads.jsonl):
         t.status == "active"
       ∧ t.check_after_ts is not None
       ∧ parse_ts(t.check_after_ts) <= now
```

| 規則 | 內容 |
|---|---|
| 掃描範圍 | **僅 `status == "active"`**（`dormant`／終態**不計**——見 §3.5 INV-1） |
| `check_after_ts` 為 `None` | 該線頭不貢獻（`active` 線頭**必須**有非 null `check_after_ts`，§2.2；此為防禦性判斷） |
| 時間解析失敗 | 該線頭**跳過**（不貢獻），**不得** raise |
| 回傳 | 純布林 |

#### 判定 2：`world_collision_detected`

**「Lived Context 管線過去 4 小時是否有新環境／真實事實輸入？」——「哪些來源算數」的明確定義如下。**

| 項目 | 規格 |
|---|---|
| **讀取目標** | `data/world/perception_trace.jsonl`（**唯一落盤的世界感知產物**）。路徑由 `WorldPerceptionTraceWriter` 以 `data_root() / "world" / "perception_trace.jsonl"` 組出 — `src/world/trace.py:36-38`；append 寫入 `:52-53` |
| **為何用此檔** | **L2 實證：原始 WorldEvent 完全不落盤**——`src/world/state.py:7-8` 明文不變量「`EPHEMERAL`：只存在 process lifetime，restart 後完全清空」／「`NO PERSISTENCE`」，`:16-21` 明寫「NOT a memory system」。**唯一可事後查核的落盤痕跡就是 `perception_trace.jsonl`** |
| **記錄 schema** | `WorldPerceptionTrace`，`src/world/perception.py:297-334`；欄位含 `event_id` / `timestamp` / `source` / `event_type` / `scores` / `accepted` / `context_injected` / `memory_written` / `novelty_id` / `selection_reason` / `extra` |
| **判定式** | `∃ r ∈ perception_trace.jsonl: parse_ts(r.timestamp) >= now - 4h  ∧  r.accepted is True  ∧  r.source ∈ SOURCES_QUALIFYING` |
| **`SOURCES_QUALIFYING`（算數的來源，窮舉）** | `weather`（`src/world/source/open_meteo.py`）、`news`／`news_event`（`src/world/source/news_rss.py`）、`calendar`／`calendar_event`（`src/world/source/calendar_ical.py`，**Bryan 日程**）。三者正是 VISION §4.3 點名的「真實天氣、新聞與 Bryan 生活事實」 |
| **不算數的來源** | 任何 `source` 不在上表者（含測試用 `synthetic`：`src/world/source/synthetic.py:117`）；`accepted is False` 者（未過感知門檻） |
| **視窗常數** | `WORLD_COLLISION_WINDOW_HOURS = 4`（本契約新增常數，**必須**具名，**不得**寫裸字面量 `4`） |
| **為何用 `accepted` 而非 `context_injected`** | `accepted`（過感知門檻，門檻 `src/world/perception.py:578` `DEFAULT_ACCEPT_THRESHOLD = 0.35`）是**管線自身**對「這是一條真實、新感知到的世界事實」的判定；`context_injected` 額外要求「當時剛好有 agent 在做回合」，會讓閘門**被無關時序綁架**。改用更嚴格的 `context_injected` 列入 §11 **OQ-4** |
| **`timestamp` 解析失敗** | 該列跳過 |
| **回傳** | 純布林 |

> ⚠️ **與既有視窗的張力（誠實記載）**：既有感知狀態的 novelty 視窗是 **24 小時**（`src/world/state.py:73` `novelty_window: timedelta = timedelta(hours=24)`，於 `:171` `if age > self.novelty_window:` 執行；`src/world/middleware.py:218` 同值），新聞來源另有 2 小時 lookback（`src/world/source/news_rss.py:167` `DEFAULT_LOOKBACK_HOURS = 2`）。本契約的 **4 小時是新常數**（工單指定），**不改動**既有 24h/2h 常數。兩者關係列入 §11 OQ-4。

> 📌 **D4 量測登記（LIFE-THREAD-CONTRACT-BOOTSTRAP-1，2026-09-17；幕僚長複核，非本票重跑）**：把上面那段「張力」放進生產數字看，張力是**可量化**的——`4h 窗 ∩ 每日 2 個 slot ⇒ 有效視窗僅 8h/天（33.3%）`；實測**合格紀錄 70/70 落在窗外**（唯一次合格史：70 筆、`2026-09-15T20:47:07–20:50:39Z`，離 night 閘門 4h 地板 `22:00Z` 差 **69–73 分鐘**）；**41 天內僅 15 天**出現過閘門可見的機會 ⇒ **自然喚醒期望值 ≈63h（mean）／48h（median）、範圍 1–6 天**。
> 🔴 **本段是量測事實的登記，不是裁定**：**不改變**本節（§4.2）的判定式、**不改變** `WORLD_COLLISION_WINDOW_HOURS = 4` 常數、**不改變** `SOURCES_QUALIFYING`。是否調整視窗（含 §11 **OQ-4** 所問的 4h vs 24h）**仍為未決，不在本票裁定**。（把這個死結解掉的第三條喚醒路徑見 §4.4。）

#### 放行邏輯（二元，無第三態）

```
should_wake := check_points_due(agent_id) OR world_collision_detected(agent_id)
```

| 情況 | 行為 | LLM 成本 |
|---|---|---|
| **兩者皆 False** | **直接保持安靜（留白）**：寫**一行** `INFO`（§9 O1），**不呼叫 LLM**、不建線頭、不改任何線頭 | **0** |
| 任一 True | 喚醒 LLM 進行**本時段的生活推進詮釋**（§5 注入範式） | **1** |

**關鍵設計後果（必須明寫）**：`world_collision_detected == False` 且無到期線頭時，**系統完全不給模型機會去編造瑣事**——這正是 VISION §4.2 要防的「LLM 積極討好陷阱」。

#### 失敗處理（與 §3.2 相反的 fail-closed 方向）

| 失敗模式 | 行為 | 理由 |
|---|---|---|
| `life_threads.jsonl` 不存在 | `check_points_due` = `False` | 新 agent 尚未有生活 → **合法留白**（VISION §2.3） |
| 該檔讀取失敗／權限錯 | `check_points_due` = `False` ＋ `WARNING` | 讀不到 ≠ 有張力 |
| `perception_trace.jsonl` 不存在／讀取失敗 | `world_collision_detected` = `False` | 無證據 ≠ 有碰撞 |
| 時間戳解析失敗 | 跳過該列 | 不猜 |

**⇒ 閘門一律 fail-silent → False（往安靜方向錯）。** 與 §3.2 溶解的 fail-open 方向相反，**兩者刻意不對稱**（理由見 §3.2 末段）。

### 4.3 與既有 gate 的關係（**不得混淆**）

| 既有元件 | `path:line` | 職責 | 與本閘門的關係 |
|---|---|---|---|
| `_inner_life_gate_check` | `src/soul/scheduler.py:340`；**唯一呼叫點** `:291-292`（在 `_publish_agency_trigger` 內，**僅 `proactive_dm`**） | 判斷「距上次內在生活事件是否 ≥ 30 分鐘」 | **正交**。它管「發訊前的最小間隔」，本閘門管「這一時段有沒有生活要推進」。**本閘門不得呼叫它、不得改它** |
| `SubmissionGate` | `src/inner_life/submission_gate.py:180` | 身份／來源完整性 accept-reject（**無數值門檻**） | **正交**。本引擎的線頭落盤**不經** SubmissionGate（線頭不是 `InnerLifeEvent`） |
| `SocialEventProducerGate` | `src/social/producer_gate.py:65` | 公開廣播隱私判定 | **正交**（且生產未接線，§11 OQ-5） |

### 4.4 冷啟動引導（bootstrap wake）——**第三條喚醒路徑（設計）**

> **一句話**：當一個 agent **零 `active` 線頭**時，讓它在**既有 slot 評估點**內、**恰一次**地主動醒來，以打破「無線頭 ⇒ 不醒 ⇒ 永無第一條線頭」的閉環。**本路徑是唯一不依賴外部世界事件的喚醒路徑。**

#### 問題（動機）：既有兩條路徑對「零線頭 agent」構成**閉環死結**

§4.2 的放行邏輯只有兩條輸入：判定 1（線頭到期檢查點）與判定 2（世界碰撞）。兩者對**零線頭的新 agent** 與**長期留白後的 agent** 都無能為力：

| # | 事實（**D4 量測登記**，幕僚長複核；非本票重跑） | 對零線頭 agent 的後果 |
|---|---|---|
| B1 | **判定 1 需要「已存在的 active 線頭」**：`∃ t ∈ fold(...): t.status == "active" ∧ t.check_after_ts <= now`（§4.2 判定 1） | 零 active ⇒ **恆 `False`**；該 agent **永無到期檢查點** |
| B2 | **兩個高流量來源恆不可通過**：`src/world/perception.py:365` 的 `TYPE_BASELINE_RELEVANCE` **無 `news_event` 鍵** ⇒ 落 default `0.10`（`:378`）⇒ `news_event` 分數 **0.3450 < 0.35**（門檻 `src/world/perception.py:578`）；`weather_temp_change`（同表基線 `0.05`）＝ **0.3300**。唯一常態可過者 `rain_started` ＝ **0.3750** | 常態**無合格世界碰撞** |
| B3 | **有效視窗僅 8h/天（33.3%）**：閘門每日只有 2 個 slot（`08:00`／`22:00`，`src/soul/scheduler.py:71-72`；時區 `America/New_York`，`configs/default.yaml:154`；`.env` 無 `SOULOS_TIMEZONE` ⇒ **12:00Z／02:00Z**）∩ `WORLD_COLLISION_WINDOW_HOURS = 4` | 一天有 **2/3** 時間的合格碰撞**閘門看不見** |
| B4 | **史上唯一一次合格紀錄 100% 落在窗外**：70 筆、`2026-09-15T20:47:07–20:50:39Z`，離 night 閘門 4h 地板（`22:00Z`）差 **69–73 分鐘** | 實測 **70/70 落窗外** |
| B5 | **41 天內僅 15 天**出現過閘門可見的機會 | **自然喚醒期望值 ≈63h（mean）／48h（median）、範圍 1–6 天** |
| B6 | `extra["summary"]`（合格紀錄的必要欄位）自 commit `9e79f2c`（2026-09-14T22:55Z）才開始寫 | 合格史僅 ~72h ⇒ **不足以等待自然發生**（修訂 1 的已知限制） |

**⇒ 閉環死結（本節存在的唯一理由）**：無線頭 ⇒ 無到期檢查點（B1）；無合格世界碰撞 ⇒ 不醒（B2–B5）；**不醒 ⇒ 永不產生第一條線頭**。而「第一條線頭」本身**才是**判定 1 的啟動條件。因此瓶頸不是「慢」（期望值 63h／48h），而是**對零線頭 agent 而言，既有機制在數學上不會自己動起來**。

#### 定位（**不得誤讀**）

| 本路徑**是** | 本路徑**不是** |
|---|---|
| **第三條喚醒路徑**（前兩條 ＝ §4.2 判定 1／判定 2） | **不是**新型 `origin_type`：值域仍**恰 4 值**（§2.3、§11 OQ-1），**不發明第五個** |
| **唯一不依賴外部世界事件**的喚醒路徑（只依「零線頭」這一個**內部狀態** ＋ **既有時段**） | **不是**新定時器／新 tick／新 sleep（INV-2）；**不是**新觸發類型／新事件型別／新排程面 |
| **orchestrator 層**的路由決策（在 M3 判定**之後**、M4 喚醒**之前**） | **不是** M3 的第三態：§4.2「放行邏輯（二元，無第三態）」**不被修改**——M3 仍只回 `True/False`，其 log 逐字不變 |

#### 觸發條件（**四項全部成立**才可 bootstrap）

| # | 條件 | 判定口徑／`path:line` |
|---|---|---|
| ① | 該 agent 的 **`active` 線頭數 == 0** | 以 M1 `fold()`（`src/soul/life_threads.py:324`）後 `status == "active"` 計。**不得**把 `dormant`／終態（`completed`/`abandoned`）計入（與 §2.6.3 計數口徑同一條；`dormant` 是「擱置」不是「不存在」） |
| ② | 該 agent 的 **bootstrap 標記未設**（見下「有界性」） | `data/soul/<agent_id>/life_thread_bootstrap.json` **不存在** ⇒ 未設；**存在** ⇒ 已用罄（永不重複） |
| ③ | **容量上限 `capacity(agent_id) > 0`** | §2.6.2 的 `capacity()`（讀 `configs/default.yaml` 的 `life_thread_capacity`，fail-closed 回 2；恆 ∈ [1,3]） |
| ④ | 在**既有 slot 評估點**（`morning`／`night`）之內 | 只在既有 2 個評估點被評估；**不新增任何計時器／評估點**（INV-2；§4.1 的「評估點數 = 2/日/agent」不變） |

#### 落點（實作位置；本票只寫契約，**0 程式碼**）

**落點在 orchestrator 層**（`src/soul/life_thread_orchestrator.py` 的 `_run_agent`），**不在** `life_thread_wake_gate.py`：

1. **先**照常呼叫 M3 `evaluate_wake_gate(...)`（`src/soul/life_thread_wake_gate.py:583`；既有呼叫點 `src/soul/life_thread_orchestrator.py:266`）並**照常記錄其二元判定**。**M3 閘門模組 0 改動**：其呼叫參數、回傳、語意與日誌**逐位元不變**。
2. **僅當**下列**全部**成立時，orchestrator 才**改以 bootstrap 路徑喚醒**（取代原本的「SLEEP 即 return」，既有 early-return 落點 `src/soul/life_thread_orchestrator.py:277-282`）：
   - M3 判定 **`should_wake is False`**；**且**
   - 其 `reason` **不是容量相關**（`REASON_ACTIVE_POOL_SATURATED`，`src/soul/life_thread_wake_gate.py:85`）；**且**
   - 條件 ①②③成立。
3. **不得**為了 bootstrap 去改 M3 的輸入或常數：`src/soul/life_thread_wake_gate.py` 為**凍結面**（本節 **0 改動**）；`WORLD_COLLISION_WINDOW_HOURS`（4h）、`TYPE_BASELINE_RELEVANCE`、門檻 `0.35` **皆不動**。
4. bootstrap 喚醒**沿用既有 M4 喚醒路徑**（同一個 `run_origin_round`），以 **§5.2.2 的既有模板**注入。

#### `origin_type` 的選定（**已拍板：`necessity_driven`**）

- **選定值**：**`necessity_driven`**（§2.3 四值之一）。
- **理由（逐字）**：其**種子來源為「時段 ＋ 角色當下處境」**（§2.3 表）——**零外部種子**，而 bootstrap 時**這兩者皆可得**（時段來自 slot，處境來自 `soul_context`）；其**語意為「生活瑣事／需求」**，正是「在沒有外部事件時，一個人仍會被自己的日常需求推著動」。
- **明文禁止**：**不得**使用或發明**第 5 個** `origin_type`。§2.3 逐字「本契約**不發明第五個**」，§11 OQ-1 已裁定不新增第五源。bootstrap 只是**既有第 2 個起源**的一條**新觸發路徑**，**不是**新起源。

#### 有界性（at-most-once，**每 agent 每 epoch 恰一次**）

| 項目 | 規格 |
|---|---|
| 次數上界 | 每 agent **每 epoch 恰 1 次**（**不是**每日、**不是**每 slot） |
| 蓋章時序 | **先蓋章再執行**——與既有 slot 章同一紀律（`_LAST_PROCESSED`，`src/soul/life_thread_orchestrator.py:231-239`）：**寧可漏一次，不可重複花費** |
| 標記落點 | `data/soul/<agent_id>/life_thread_bootstrap.json`（**新增檔**）。路徑**由 M1 的 `life_threads_path(agent_id)` 導出其 per-agent 目錄**（`src/soul/life_threads.py:236-242`：該函式內部即 `src/paths.py` 的 `data_root()` 慣例，§2.1，**並含 `_validate_agent_id` 的安全路徑段驗證**）⇒ orchestrator 對 `life_threads_path` 的呼叫**恰 1 次、且僅作為 `bootstrap_marker_path(...)` 的直接引數**（護欄 `test_a5` **恰等值演化**，非放寬）；**不得**硬編碼 `data/`、**不得**自行拼接 agent_id 路徑段（那會繞過路徑段驗證）；**不得**改動任何**既有**狀態檔（`life_threads.jsonl` 等）的**格式** |
| 標記內容（至少） | `bootstrapped_at`（**ISO-8601**，帶時區）＋ `count`（**整數 `1`**） |
| 讀／寫失敗 | **⇒ 不 bootstrap**（fail-quiet）：讀不到 ⇒ 無法確認條件②，**不執行**；寫不進去 ⇒ 無法保證 at-most-once，**不執行**。**永不重試放大**、**永不 raise** |
| 「epoch」的定義 | 由**標記檔的生命週期**界定：標記**存在 ⇒ 該 epoch 已用罄**。本機制**不自動重置**（不按日、不按 slot、不按重啟）。清除標記 ＝ **Owner／維護票**的明示動作，不是例行行為 |

#### 旗標（**預設關**）

| 項目 | 規格 |
|---|---|
| 名稱 | **`LIFE_THREAD_BOOTSTRAP_ENABLED`** |
| 讀取時機 | **呼叫時讀取**（每次呼叫重新讀 `os.environ`，**非**匯入時快取）——沿用既有同型先例 `consolidation_enabled()`（`src/soul/life_thread_consolidation_wiring.py:152-164` 逐字「每次呼叫都重新讀 `os.environ`」） |
| 真值集合 | `{"1","true","yes","on"}`（**不分大小寫**，去首尾空白後比較；同 `TRUTHY_VALUES`） |
| 缺席／非真值／讀取失敗 | **⇒ OFF**（fail-safe 方向 ＝ 不花錢） |
| `.env`／`configs/**` | **不得**寫入此變數（落地後**預設休眠**） |
| 啟用 | 屬 **Owner 決定**：設 env ＋ **一次重啟**（同「修訂 1」的「需重啟才生效」性質） |

#### 成本上界（**必須可反證**）

- bootstrap **每 agent 恰 1 次** ⇒ 全機（**10** 個 agent）一次性 **≤ 10 次** origins 輪。
  ※ **上界的取法（可查核）**：configs/default.yaml 恰 10 個 - id:（生產 morning 輪去重亦恰 10 個 agent：agent_akane/anna/aoi/mahiru/mai/miku/ram/rem/ruka/yua）；實務上界只會更小，因須同時滿足觸發條件①②③。
- 以 `reasoning_effort:"none"` 實測 **≈170 tokens／輪**估 ⇒ **一次性 ≈1,700 tokens**（10 × 170 ＝ 1,700），**之後回到正常節奏**（§8.2／§8.3 的成本表不變）。
- **不得**因此新增任何**重試**：該輪失敗即失敗（fail-quiet），標記已蓋 ⇒ **不補**。

#### 不得觸碰（凍結與範圍界線）

- `WORLD_COLLISION_WINDOW_HOURS`（**4h 不動**）。
- `TYPE_BASELINE_RELEVANCE`／門檻 `0.35`（**感知顯著性校準不在本節範圍**）。
- `src/soul/life_thread_wake_gate.py`（**凍結**）、`src/soul/life_thread_dissolution_exec.py`（**凍結**）。
- §2.3 的 4 值、§4.1／§4.2 的判定式與常數、§3／§5 的任何內容、§11 OQ-4 的裁定（**皆不在本節範圍**）。

**已知限制（誠實登記）**：①若 origins 的必填種子源為空（例如該 agent 完全無 diary 史）⇒ M4 現行的 `collect_necessity_seeds` 會**退回中立錨點** `WHIM_NEUTRAL_ANCHOR`（`src/soul/life_thread_origins.py:411-412`）⇒ `prompt_available` 仍為 True、**仍會 bootstrap**，但**不得宣稱任何具體外部事件**（＝不捏造事實；此為**唯一**允許的退化，由 M4 現行行為保證，本引擎**不**為此改動 M4）。由於 diary 的 `_fire_all`（`src/soul/scheduler.py:1658-1660`）在同一 tick 內早於 `_fire_life_thread_slot`（:1694）執行，**正常路徑**下同一 tick 即可取得真實處境。②標記的蓋章時點在「§4.4 觸發條件全部成立」且「M4 的 soul_context 非空（避免 0 成本的空轉被消耗）」之後、**origins 輪之前**；若該輪因 prompt_available == False 或 LLM 失敗而未能產出線頭，該 agent 的一次性 bootstrap 即被消耗（**fail-quiet、不重試**）。③重新武裝的唯一方式＝刪除該 agent 的 data/soul/<agent_id>/life_thread_bootstrap.json（**由 Owner 執行**，agent 不得自行刪改生產 data/**）。

#### 與既有條文的邊界（**本節未修改任何既有條文**）

1. **§7 INV-3**（逐字：「當 `check_points_due == False` 且 `world_collision_detected == False` 時，該時段**LLM 呼叫數 == 0**」）與 **§8.1**（逐字：「本引擎唯一會呼叫 LLM 的**兩條路徑**」／「除此之外本引擎 **0 條** LLM 路徑」）：在旗標**缺席／OFF（＝預設）**時，兩者**逐字仍真**——bootstrap 完全不執行，生產行為與本節引入前相同。旗標**啟用**後，bootstrap 會成為**第 3 條**、且**每 agent 一次性 ≤1 次**的 LLM 路徑，此即該兩處既有文字的**例外**。**本節未修改 §7／§8 任何字**；該兩節其後已由 **修訂 3（`LIFE-THREAD-CONTRACT-BOOTSTRAP-FUP-1`，2026-09-17）同步為「旗標條件式例外」**（詳見 §4 開頭修訂註記）——即本項例外**已正式入約**，不再待裁定。
2. **§4.2「放行邏輯（二元，無第三態）」**：**不被修改**。M3 仍只回 `True/False`、log 逐字不變；bootstrap 是 **orchestrator 層**在 `should_wake is False` **之後**的分支，**不改** M3 判定式、**不改**其輸入、**不改**其常數。
3. **§5.2.2 必填上下文的可得性（L2 實證）**：`_fire_all`（diary 落盤，`src/soul/scheduler.py:1658-1660`）在 `_fire_life_thread_slot`（`src/soul/scheduler.py:1694`）**之前**執行 ⇒ bootstrap 評估的同一 tick 內，**當前時段的 diary 已落盤**，故「近 3 日 `source=="llm"` 條目」在**正常路徑可得**。**殘餘邊界**：若某 agent **完全無 diary 史**（例如註冊後首輪即 bootstrap），§5.2.2 的必填上下文不完整——該情形之處置已由 **修訂 3 定案**：**fail-quiet（不 bootstrap、不捏造種子）**，並登記於上方「已知限制（誠實登記）」①。

#### 可測斷言（設計，供後續實作票驗收）

| # | 斷言 | 形式 |
|---|---|---|
| BS-1 | 旗標 OFF（含缺席）⇒ 本節**完全不執行** | mock LLM 計數 == 0；`life_thread_bootstrap.json` **不被建立** |
| BS-2 | 旗標 ON ＋ 條件①②③成立 ＋ 在 slot 內 ＋ `should_wake is False`（reason ≠ 容量）⇒ **恰 1 次** | 第 1 次評估 ⇒ 1 輪 origins；**第 2 次評估（同日／隔日／跨重啟）⇒ 0**（標記已蓋） |
| BS-3 | 條件②（標記）為**唯一的持久防重** | 標記檔存在 ⇒ 恆不 bootstrap（跨重啟亦然） |
| BS-4 | 標記讀／寫失敗 ⇒ **不 bootstrap** | 注入 IO 例外 ⇒ 0 LLM、0 新標記、**不 raise** |
| BS-5 | **M3 逐位元不變** | `evaluate_wake_gate` 的呼叫參數、回傳與 log 與本節引入前相同（`should_wake is False` 時其 `reason` 仍照原樣產生與記錄） |

---

## §5 模組 4：起源 Prompt 注入範式

**目的**：讓角色有「**在生活／在忙瑣事／在想念／在發呆**」的立體節奏，並以既有 Lived Context 作為「世界碰撞驅動」的種子，**杜絕單機自嗨**（VISION §4.3 逐字：「杜絕純腦內單機幻覺」）。

### 5.1 喚醒輪次的 prompt 契約（共同骨架）

一次閘門放行 ＝ **恰好 1 次 LLM 呼叫**，產出**同時**完成兩件事的單一 JSON：

1. **生活推進詮釋**：對到期線頭推進敘事（或判定其 `completed`/`abandoned`）。
2. **可能誕生新線頭**（`created`）——僅在 `active_count < capacity(agent_id)` 時。

```json
{
  "actions": [
    {"thread_id": "<existing uuid|null>", "op": "advance|complete|abandon|create",
     "title": "<1~40 字元，op=create 時必填>",
     "narrative_content": "<1~600 字元>",
     "origin_type": "goal_driven|necessity_driven|whim_driven|world_collision",
     "share_target": "none|lounge|agent_<id>|user_bryan",
     "next_check_hours": "<整數 1~72>"}
  ]
}
```

| 規則 | 內容 |
|---|---|
| `actions` 長度 | 1–3；**超過 3 截斷至前 3 項**（防單輪噴發） |
| `op == "create"` 且 `active_count >= capacity` | **丟棄該 action**（不落盤），其餘照寫（§2.6.3） |
| `next_check_hours` → `check_after_ts` | `now + timedelta(hours=next_check_hours)`，**clamp 至 [1, 72]** |
| `op == "complete"/"abandon"` | 觸發 §3 溶解（**同輪或下一輪**，見下） |
| LLM 失敗／非 JSON | **fail-silent**：本時段**不落盤任何事件**、寫 `WARNING`；線頭狀態不變（下一時段可再試） |
| **0 數值打分** | 輸出 schema **不得**含任何浮點評分欄位；`next_check_hours` 是**整數時距**（排程參數），不是強度分數 |

**溶解的呼叫時序（裁定）**：`op == complete/abandon` 落地後**立即**呼叫 §3 溶解（**同一輪內，第 2 次 LLM 呼叫**）。理由：張力解除後立即退役是 VISION §4.1 的逐字要求（「張力解除後**立即**退役並溶解」），延後會讓終態線頭在檔中堆積。**成本已由 §3.6 的 `DISSOLVE_MAX_PER_DAY` 封頂**（見 §8）。

### 5.2 逐 `origin_type` 注入模板

#### 5.2.1 `goal_driven`（目標驅動）

| 項目 | 規格 |
|---|---|
| 種子來源 | `goals` 表（**唯讀**）：`src/goals/models.py:117`、`src/goals/motive_provider.py:115-116` |
| 必填上下文 | 該 agent 的 `ACTIVE`/`IN_PROGRESS` goal 之 `title`／`state`（最多 3 條）；`soul_context` |
| 禁止事項 | **不得**修改 goal 狀態；**不得**把 goal 直接複製成線頭 `title`（線頭必須是**具體生活張力**，不是意向複述——VISION §6「Goals 是長程意向燈塔，線頭是具體生活張力」）；**不得**讀其他 agent 的 goals |
| 模板 | 「你的長期意向之一是「{goal_title}」。**這幾天你在這上面實際動手了嗎？** 如果沒有，那是因為什麼具體的事卡住了？如果有，進展到哪一步？」 |

#### 5.2.2 `necessity_driven`（需求驅動）

| 項目 | 規格 |
|---|---|
| 種子來源 | 當前時段（`morning`/`night`）＋ 角色當下處境；**既有 diary 的近況**（`data/soul/<agent_id>/diary/*.jsonl`） |
| 必填上下文 | 當前本地時間與時段；近 3 日 diary **`source == "llm"`** 的條目（沿用既有 `source=="llm"` 過濾，`src/llm/proxy.py:301`） |
| 禁止事項 | **不得**宣稱「沒有選擇要不要開始」（VISION §4.3：需求驅動「沒有選擇要不要開始，只有選擇怎麼解決」→ prompt 必須**預設事情已存在**，問「怎麼解決」而非「要不要做」）；**不得**要求外部工具呼叫 |
| 模板 | 「現在是{時段}。有些事不是你能選要不要發生的——{具體處境，如天氣轉涼／身體疲累／東西壞了}。**你打算怎麼處理它？**」 |

#### 5.2.3 `whim_driven`（心血來潮驅動）

| 項目 | 規格 |
|---|---|
| 種子來源 | 角色個性（`load_persona(agent_id)` 的 `soul_context`） |
| 必填上下文 | `soul_context`（第一人稱風格來源，VISION §4.9） |
| 禁止事項 | **不得**使用 Lived Context／外部事實（此源必須**純內生**，否則與 `world_collision` 重疊）；**不得**輸出通用雞湯（必須是該角色**獨有**的偏好動作） |
| 模板 | 「依照你的個性，有什麼**只有你才會**突然想做的事？不是為了誰，也不是因為發生了什麼——就是忽然想。描述你**當下正在做**的那個動作與心裡的念頭。」 |

#### 5.2.4 `world_collision`（Lived Context 世界碰撞驅動）

| 項目 | 規格 |
|---|---|
| 種子來源 | `world_collision_detected == True` 所依據的那批 `accepted == True` 記錄之 **fact text ＝ `extra["summary"]`**（`event_type`／`source` 只作**標籤／來源過濾**用，**不得當文字用**），從 `data/world/perception_trace.jsonl` 取得（欄位定義見附錄 A.4；來源修訂見「修訂 1」） |
| **fact text 取得規則** | **僅**取 `accepted == True` **且** `extra["summary"]` **存在、為字串、去空白後非空** 的記錄。**缺欄／空字串／純空白 ⇒ 跳過該事件**（只累計有界計數後**繼續**，0 raise）；**絕不以 `event_type`／`novelty_id`／`reason` 頂替、絕不捏造**。若命中記錄**全部**無 fact text ⇒ **本次不產生 `world_collision` 線頭**（寧可留白，不得編造） |
| 必填上下文 | §4.2 判定 2 命中的**具體事實文字**（不得只給「有新事件」）；當前時間；`soul_context` |
| 禁止事項 | **不得**在無命中的情況下捏造天氣／新聞／日程（此為 VISION §4.3「杜絕純腦內單機幻覺」的執行點）；**不得**把世界事實寫成「Bryan 對我說的話」（世界事件不是對話）；**不得**跨 agent 洩漏（見 INV-4）；**不得**以 `event_type`／`novelty_id`／`reason` 頂替 fact text（它們分別是標籤／去重鍵／判定理由，**不是**事實文字） |
| 模板 | 「剛剛世界發生了這件事：{fact_summary}。它跟你**自己的生活**有什麼關係？你**當下**因此改變了什麼動作？」 |

> **界線說明（WORLD-FACT-TEXT-PERSIST-1, 2026-09-14；即「修訂 1」）**：世界層**仍不持久化 world state、也不回讀**。`src/world/state.py:7-8`, `:16-21` 的 `EPHEMERAL`／`NO PERSISTENCE`／`NOT a memory system` 不變量**範圍不變**——它講的是 **world state**（in-memory deque，重啟即清空），**不是**感知 trace。本變更只是讓**既有**感知 trace（`WorldPerceptionTrace`，既有 artifact）的**既有** `extra` dict 帶一份**事實文字副本**（僅 `accepted == True`、截斷 ≤ 200 字元、空／純空白不寫鍵），供本引擎取用。**0 新檔、0 新 artifact、0 新 schema 版本、0 新儲存面**；且 `accepted`／`scores`／`novelty_count_in_window`／`context_injected`／`memory_written` 的判定語意**逐位元不變**（實作落在 `src/world/middleware.py:97-117`，兩個既有寫入點見附錄 A.4）。

### 5.3 「立體節奏」的可測條件

工單要求「讓角色有『在生活／在忙瑣事／在想念／在發呆』的立體節奏」。**可測形式（設計）**：在任一 7 日窗口內，某 agent 的 `life_threads.jsonl` 的 `created` 事件中 **`origin_type` 不得恆為單一值**（即 `len(distinct origin_type) >= 2`），**除非**該 agent 同期 `created` 事件總數 ≤ 1（樣本不足時不強制）。

> ⚠️ **此條件必須標為「軟性驗收」，不得當硬斷言**——VISION §8 Non-Goals 明列「**不強迫天天有線頭**」。硬性部分僅止於「**不得**由排程器指定內容」（§7 INV-8）。

---

## §6 模組 5：與既有系統的乾淨介接

### 6.1 M7-1 `ACTIVITY_POOL` 退役路徑（分階段、可回滾）

#### 6.1.1 現況定位（L2 實證）

| 項目 | `path:line` | 內容 |
|---|---|---|
| **定義** | `src/soul/dream_event.py:220-231` | 模組級常數 **list of 10 dicts**，每項 `{"name","category","shareable"}`。**全 agent 共用**（**無** per-agent keying、**無** 權重） |
| 組成 | `:220-231` | `shareable=True` 6 項（工作／做飯／吃東西／運動／創作／散步）；`shareable=False` 4 項（看書／整理房間／聽音樂／發呆） |
| **消費者（`src/` 內僅 2 處）** | `src/soul/dream_event.py:669`（`DreamEventWriter.write_event`，def `:644`）：`activity = random.choice(ACTIVITY_POOL)` | 自身事件 |
| | `src/soul/scheduler.py:1032-1034`（`SoulScheduler._fire_shared_event`，def `:1016`）：`from src.soul.dream_event import ACTIVITY_POOL, get_dream_event_writer` → `activity = random.choice(ACTIVITY_POOL)` | 共用事件 |
| 下游客（消費其**落盤產物**而非符號） | `src/soul/scheduler.py:1215` `_get_recent_shareable_activity(agent_id)`（過濾條件 `:1260` `slot != "event"` → `:1262` `shareable is not True` → `:1264` `source != "llm"`），呼叫點 `:1607` | 活動驅動主動傳訊 |
| 下游客 2 | `src/io/gateway.py:516,532`（`/api/soul/status` 的 Soul Wall，`:532` `recent_activity`） | 觀測面 |
| 觸發時機 | `src/soul/scheduler.py:1665-1666`（`_is_event_time` → `_fire_event`；4–8h，常數 `:139-140`）；`:1668-1669`（`_is_shared_event_time` → `_fire_shared_event`；6–12h，常數 `:172-173`） | 兩個**既有** timer |
| 無 per-agent 分化 | `docs/INNER-LIFE-AGENCY-PLAN.md:154` 的 `PERSONA_ACTIVITY_WEIGHTS` **僅為提案**，`src/` 不存在 | — |

#### 6.1.2 三階段退役路徑（**每階段可獨立回滾**）

| 階段 | 名稱 | 行為 | 回滾方式 |
|---|---|---|---|
| **P1｜雙軌唯讀** | Shadow Read | `_get_recent_shareable_activity`（`scheduler.py:1215`）**保持不變**；另新增唯讀函式 `active_thread_activity(agent_id)` 讀**活躍線頭當前狀態**，並寫一行比對 `INFO`（兩者是否一致）。**`ACTIVITY_POOL` 仍是唯一供給者**（0 行為變更） | **刪除新函式與該行 log** → 回到現狀 |
| **P2｜線頭優先** | Thread-Preferred | 當 `active_thread_activity(agent_id)` 回非 `None` 時，**優先**用它；否則 fallback 回 `ACTIVITY_POOL`。**雙軌並存** | 把新增分支的條件改為 `False`（或刪除分支）→ 立刻回到 P1 行為 |
| **P3｜抽籤退役** | Pool Retired | `random.choice(ACTIVITY_POOL)` 的**呼叫點**（`dream_event.py:669`、`scheduler.py:1034`）改為讀活躍線頭；`ACTIVITY_POOL` **常數本身保留不刪**（`src/soul/dream_event.py:220-231`） | 恢復呼叫點為 `random.choice(ACTIVITY_POOL)` → 回到 P1/P2 |

**P3 的硬約束（可測）**：
- **常數不刪除**：`ACTIVITY_POOL` 符號必須繼續存在且 `len(ACTIVITY_POOL) == 10`（既有測試 `tests/test_m7_1_activity_model.py:58` 的斷言因此**不需改動** → 0 測試改動）。
- **fallback 永久保留**：活躍線頭為空時**必須** fallback 回 `ACTIVITY_POOL`（否則「0 線頭」會讓 event 日記寫不出來 → 反而製造新的斷鏈）。
- **回滾不需資料遷移**：三階段皆**不新增／不刪除任何資料欄位**，故回滾只涉及程式碼分支，**0 資料修復**。
- **本契約 0 實作**：以上為**設計**；實作須另開工單（§10）。

**P3 之後 `ACTIVITY_POOL` 的定位**：降級為**「無生活時的保底活動池」**（fallback only），**不再是主要供給**。這使「靜態抽籤 → 讀取活躍線頭當前狀態」的替換**可逆且無懸崖**。

### 6.2 🔴 發訊情境投影：錨 A（生活由來）注入點（**本契約最關鍵的介面**）

> **性質**：當線頭自然產生 `share_target == "user_bryan"` **且跨越心理阻力**（§6.3 可判定）時，把該線頭的 `narrative_content` 作為**生活由來（錨 A）**送入交付鏈。

#### 6.2.1 交付鏈全貌（逐跳 `path:line`，L2 實證）

**真實嵌套順序（L2 實證，經逐跳核對）**：`_run_loop:1677` → `_fire_proactive_dm:1425`（G2–G9 閘門）→ `_publish_agency_trigger:254` → **在其內部** `_inner_life_gate_check:292` → `_decision_check:300` → `bus.publish:332` → handler → executor → `consciousness._fire_intent:419` → middleware → token manager → `proxy._handle_event_impl:3560` → `_build_messages_private:1256` → LLM → `AGENT_SPEAK:4019` → router → `telegram.send:277`。**⇒ `_decision_check` 是被 `_publish_agency_trigger` 呼叫的，不是它的前置。**

| # | 階段 | `path:line` | 職責 |
|---|---|---|---|
| 0 | 主迴圈 | `src/soul/scheduler.py:1677-1678`（判據 def `:1132-1139`） | `if self._is_proactive_dm_time(now): await self._fire_proactive_dm()` |
| 1 | 候選與閘門 | `src/soul/scheduler.py:1425` `_fire_proactive_dm()`；`:1452` `_get_proactive_agents()`；G3 `:1461`／G4 `:1484`／G5 `:1529`（**已非阻斷，僅 log**）／G6 `:1539`／G7 `:1565`／G7b `:1589` | G2–G9 |
| 2 | 觸發發布 | `src/soul/scheduler.py:254` `_publish_agency_trigger()`；**其內 `:291-292` `_inner_life_gate_check` → `:300` `_decision_check`（def `:409`）**；`:332` `bus.publish` | 發布 `AGENCY_TRIGGER` |
| 3 | 決策分流 | `src/soul/scheduler.py:483-492`（`if result.transmit:` `:483` → `:484` `mark_transmitted` → `:492` `return True`）；`:315-317` `extra_out["motive_target"]` | `DecisionResult` 消費 |
| 4 | Handler | `src/agency/trigger_handler.py:84` `handle_event()`；`:110` `run_agency(...)`；`:124` `if result.decision.should_act:`；`:132` `await self.llm_executor(...)` | Agency 4 stages |
| 5 | **生成入口** | `scripts/run_server.py:1174` `_proactive_dm_llm_executor()`；draft 組裝 `:1213-1219`；`:1258` `resolve_proactive_delivery`（def `:270-282`）；`:1260-1262` `_chrono_payload = {"draft": _draft}`；`:1276-1281` `_fire_intent(...)` | 組 `_draft` |
| 6 | 意圖廣播 | `src/agent/consciousness.py:419` `_fire_intent()`；`:440-441` `intent_payload["draft"] = chrono_payload["draft"]`；`:492` publish `AGENT_INTENT` | 透傳 draft |
| 7 | Prompt 輸入 | `src/llm/proxy.py:3560` `_handle_event_impl()`；`:3563` `reason`；`:3564` `draft`；`:3573` `motive_target`；`:3592`/`:3596-3597` `user_message = draft` | 取 draft |
| 8 | **draft 的實際去向** | 🔴 `src/llm/proxy.py:3705-3710` **把該 `role="user"` 的 draft 訊息 pop 掉**；`:3723-3730` 改以**獨立 system 訊息** `[主動觸發標記]` 重新發出（draft 被引號包住，`:3727`）；`:3739` 尾端 `role="user"` 改為固定佔位句「（你主动发起讯息，不是回应任何人）」 | **主動輪的 draft 不是尾端 user 訊息**，而是 `[主動觸發標記]` system 區塊內的一段引文 |
| 9 | **Prompt 組裝（system）** | `src/llm/proxy.py:1256` `_build_messages_private()`；`:1292` `system_parts = [identity_anchor + soul.strip()]`；`:1431` `system_parts.append(temporal_block)`；`:1433` `messages.append({"role":"system", …})`；呼叫點 `:3685`（group 為 `:3683`） | 逐塊組 `system_parts` |
| 10 | 送出 | `src/io/channels/router.py:185` `_on_agent_speak()`（訂閱 `:163-166`）；`:344-348` `adapter.send(...)`；`:350-356` 送達行 | 依 `target_channel` 分發 |
| 11 | 網路送出 | `src/io/channels/telegram.py:277` `send()`；`:285` `bot.send_message` | 實際出網 |
| 11b | Web 路徑（A2A／group 實際走這條） | `src/io/gateway.py:400-405` 訂閱；`:956` `_on_agent_speak`；`:1010` `manager.broadcast` | WS 廣播 |

#### 6.2.2 注入點與資料形狀（**逐字**）

| 項目 | 值 |
|---|---|
| **注入點（private 路徑）** | **`src/llm/proxy.py:1353-1360`** —— `[最近內在生活]` 區塊 |
| **注入點（group 路徑）** | **`src/llm/proxy.py:719`**（同一 helper 的另一呼叫點） |
| **資料供給函式** | `src/llm/proxy.py:250` `_format_recent_inner_life(agent_id) -> str` |
| **資料來源（現況）** | `data/soul/{agent_id}/diary/{YYYY-MM-DD}.jsonl`，近 `INNER_LIFE_DAYS` 日 |
| **現況常數** | `src/llm/proxy.py:134` `INNER_LIFE_DATA_DIR = str(data_root() / "soul")`；`:135` `INNER_LIFE_DAYS = 3`；`:136` `INNER_LIFE_MAX_ENTRIES = 5`；`:137` `INNER_LIFE_MAX_CHARS_PER_ENTRY = 60` |
| **現況過濾** | `:293` `slot ∈ ("morning","night","dream","event")`；`:301` `entry.get("source") != "llm"` → skip（**placeholder 不得注入**） |
| **現況行格式** | `:309` `out_lines.append(f"- [{date_str} {slot}] {content}")`；`:312` `"\n".join(out_lines[-INNER_LIFE_MAX_ENTRIES:])` |
| **注入塊逐字** | `:1356-1359`：`"\n[最近內在生活] 以下是你最近的日記/夢境/事件摘要。這些都是你自己經歷過的事情, 根據對話上下文自然運用即可, 不要逐條複述或解釋, 也不要重複 tag。\n## 你的最近內在生活\n{inner_life}\n"` |
| **回傳（資料形狀）** | **單一多行字串**；每行 `- [<YYYY-MM-DD> <slot>] <content ≤60 字元>`，**LF 分隔**；空字串 ⇒ 呼叫端**不注入**（`:1354` `if inner_life:`） |
| **無資料時的行為** | `:310-311` `if not out_lines: return ""` → 呼叫端跳過該塊。**fail-silent，不 raise** |

**⇒ 生活線頭 `narrative_content` 必須具備的資料形狀**：一個**可併入上述多行字串**或**作為緊鄰同構區塊**的**純文字行**。具體（設計）：

```
- [<YYYY-MM-DD> thread] <narrative_content 截斷至 THREAD_ANCHOR_MAX_CHARS>
```

- `THREAD_ANCHOR_MAX_CHARS = 60`（**對齊**既有 `INNER_LIFE_MAX_CHARS_PER_ENTRY = 60`，`src/llm/proxy.py:137`——**不新增第二套截斷語意**）
- 標記 `<slot>` 位置寫字面 `thread`，使錨 A 的來源在 prompt 中**可被事後辨識**（觀測點 §9 O4）
- **合併位置**：在 `_format_recent_inner_life` 的 `out_lines` **之後**（即 `:312` 的 `out_lines[-INNER_LIFE_MAX_ENTRIES:]` 之前），把活躍線頭行**插入**；總行數上限維持 `INNER_LIFE_MAX_ENTRIES = 5`（**不提高**，避免 prompt 膨脹）
- **不得**改動 `:1356-1359` 的注入塊逐字文案（避免破壞既有 prompt 錨點測試）；線頭行走**同一區塊內**

#### 6.2.3 為何這是「本契約最關鍵的介面」

現況錨 A 的**供給率結構性不足**（L2 引證 `docs/TRANSMIT-GROUNDING-SPEC.md` §1.4）：近 7 日 10 位角色合計僅 **7 筆**可作活動種子的 entry（≈ **0.1 筆/角色/日**），而 proactive_dm 目標節奏是 **5–8 條/日**。**卡點是 `shareable: false`**（`src/soul/scheduler.py:1262` 擋下；該票實測今日 `shareable:true` = **0/10**）。

**⇒ 本引擎的貢獻**：線頭的 `narrative_content` **不依賴 `shareable` 旗標**——它由 §4 閘門與 §6.3 阻力判定決定是否對外，**供給來源與 diary 的 event slot 解耦**。這是把「錨 A 供給率」從 ≈0.1/日 拉起來的**唯一機制**。

### 6.3 「心理阻力」的可判定定義（**二元，否則不得寫入契約**）

VISION §5.1 逐字定義了兩層阻力：**客廳同伴（向內）＝同維度隨口搭話（Ambient Interaction），阻力低、隨興自然**；**主動找 Bryan（向外）＝跨越世界維度進入現實生活打擾主人（Cross-Dimensional Reachout），需具備顯著的心理門檻與情感重量**。並明列「**拒絕扁平平權四選一**」。

**⇒ 可判定規則（設計，純布林，無分數）：**

```
crosses_resistance(thread) -> bool
  := thread.share_target == "user_bryan"
   ∧ thread.origin_type in {"necessity_driven", "world_collision"}   # 客觀錨定型：有外部事實依據
   ∧ thread.status == "active"
   ∧ thread.terminal_status is None            # 尚未終態
```

| 構成項 | 為何是二元的 |
|---|---|
| `share_target == "user_bryan"` | 枚舉等值比較（**不是**「有多想說」的分數） |
| `origin_type ∈ {necessity_driven, world_collision}` | **離散集合成員判定**。語意：這兩源帶有**客觀的外部錨**（生活必需／世界碰撞），符合 VISION「需具備顯著的心理門檻與情感重量」中的「重量」＝**有事實可指**，而非空泛想念 |
| `status == "active"` | 枚舉等值 |
| 未終態 | `dissolved_at is None` 等值判定 |

**明列為「不跨越阻力」的情形（必須安靜）**：
- `origin_type == "whim_driven"`：純心血來潮**不足以**跨越世界維度打擾主人（此為本契約對 VISION §5.1「防止 Bryan 被瑣事轟炸」的落地）；
- `share_target ∈ {none, lounge, agent_<id>}`：向內搭話走**既有**客廳路徑，**不進入** 1:1 交付鏈。

> ⚠️ **本判定的複雜度刻意壓到最低**（4 個等值／成員判定）。**任何形式的「想念強度」「重要程度」數值一律禁止**（VISION §2.4）。若 Owner 認為需要更豐富的阻力模型，列 §11 OQ-6。

**與既有防線的關係（0 新增通道）**：本投影**不新增任何投遞通道**。`user_bryan` 的實際送達仍走**既有** proactive_dm 交付鏈（§6.2.1），因此**既有全部外圍防線原樣生效**：G3 冷卻窗 7200s（`src/soul/scheduler.py:155`／`:1461`）、G4 靜音時段 23:00–08:00（`:156-157`／`:1484`／`:1166`）、G7 longing 門檻（`:87`／`:1565`）、G7b 每角色每日上限 1 則（`src/soul/proactive_policy.py:30`／`src/soul/scheduler.py:1589`）、`_inner_life_gate_check` 30 分鐘間隔（`src/agency/inner_life_gate.py:129`／`:350`）。**VISION §8「過渡期治理原則」逐字要求既有外圍安全防線嚴格維持在位、絕不提前拆除 → 本契約 0 變更。**

---

## §7 不變量與防線（逐條可測）

| # | 不變量 | **可硬斷言形式** |
|---|---|---|
| **INV-1** | **防 Goals v2 膨脹**：終態（`completed`/`abandoned`）與 `dormant` 線頭**永不**被閘門主動調度 | `check_points_due(agent)` 對僅含 `dormant`/終態線頭的 agent **恆回 `False`**；fold 後 `status == "active"` 的線頭數 `<= LIFE_THREAD_ACTIVE_CAP_HARD_MAX`（=3） |
| **INV-2** | **0 新定時器 / 0 新 tick / 0 新 sleep** | 全 repo `grep -E "threading\.Timer\|APScheduler\|apscheduler\|BackgroundScheduler\|loop\.call_later\|call_later"` 命中數**不增加**（現況 **0**）；本引擎相關檔案 `grep "create_task"` 命中 **0**；`src/soul/scheduler.py` 的 `asyncio.sleep` 呼叫數**不增加**（現況 2 處：`:1696`、`:1702`） |
| **INV-3** | **安靜時 0 LLM 呼叫（預設；旗標 ON 時至多 1 次 §4.4 bootstrap）** | 當 `check_points_due == False` 且 `world_collision_detected == False` 時：旗標 LIFE_THREAD_BOOTSTRAP_ENABLED 缺席／OFF（預設）⇒ 該時段 LLM 呼叫數 == 0；旗標 ON ⇒ 恰 0 或恰 1，且為 1 時必須是 §4.4 的 bootstrap 例外、且該 agent 的 bootstrap 標記在該次之前未設（每 agent 每 epoch 恰一次）（以 mock LLM 計數斷言）；且**檔案列數不變**（`life_threads.jsonl` 的 `stat().st_size` 前後相等） |
| **INV-4** | **per-agent 隔離**（**以路徑分割實作，不依賴 IdentityFirewall**——後者生產未接線，`scripts/run_server.py:495-498` ＋ `src/inner_life/submission_gate.py:333`） | 讀寫 API **只接受單一 `agent_id`** 參數；`agent_A` 的呼叫**永不開啟** `data/soul/agent_B/**`（以 monkeypatch `Path.open` 記錄開啟路徑斷言）；寫入 dict 的 key 集合**不含** `agent_id`（身分由路徑承載，避免偽造） |
| **INV-5** | **0 數值打分** | `life_threads.jsonl` 與 SAGE `Fact` 的寫入 key 集合**不含** `score`/`weight`/`intensity`/`urgency`/`priority`/`longing`；§5.1 輸出 schema 的浮點欄位數 == **0**；`Fact.confidence` **恆為常數 1.0**（非 LLM 產生） |
| **INV-6** | **狀態機封閉性** | `allowed_transition("completed", X) is False` 且 `allowed_transition("abandoned", X) is False` 對所有 `X`；非法轉移**不寫列**（檔列數不變）＋ 回傳 `False` ＋ **不 raise** |
| **INV-7** | **留白是成功**：不得以產出量當驗收 | 驗收測試中**不得**出現「線頭數 > 0」或「傳訊數 > 0」形式的 assert；合法 assert 僅為 §9 的判定正確性 |
| **INV-8** | **排程器不指定內容** | `src/soul/scheduler.py`（及任何排程路徑）**不得**出現任何線頭 `title`／`narrative_content` 的**字面字串或模板**；內容字串**只**能來自 LLM 輸出。AST 斷言：scheduler 模組的常數字串集合**不含**任何 `origin_type` 以外的內容樣板 |
| **INV-9** | **Frozen Contract 0 改動** | `git diff --name-only` **不含** `src/agency/**`、`src/inner_life/submission_gate.py`、`src/memory/sage/**`、`src/soul/decision.py`（`DecisionResult`／`DECISION_ACTIONS`）；`DECISION_ACTIONS` 值元組逐字不變（`src/soul/decision.py:95`） |
| **INV-10** | **`ACTIVITY_POOL` 常數不刪除** | `from src.soul.dream_event import ACTIVITY_POOL` 可 import；`len(ACTIVITY_POOL) == 10`（既有測試 `tests/test_m7_1_activity_model.py:58` **不需改動**） |
| **INV-11** | **fail 方向對稱性** | 閘門讀取失敗 → **False（安靜）**；溶解失敗 → **不寫 `dissolved_at`、不寫 SAGE（可重試）**。兩者可各以一個 monkeypatch 例外注入測試斷言 |

---

## §8 成本模型（**Owner 對成本敏感，本章為契約必要章節**）

### 8.1 呼叫路徑清單（**兩條常態路徑 ＋ §4.4 bootstrap 例外；後者預設關**）

| 路徑 | 觸發 | `path:line`（掛載點） | 每次呼叫數 | 每日上限機制 |
|---|---|---|---|---|
| **A｜生活推進詮釋** | §4 閘門 `should_wake == True` | `src/soul/scheduler.py:1687` 之後新增一行（設計）；守門 `:1753` 同構 | **1** | `LIFE_THREAD_WAKE_MAX_PER_DAY = 2`（＝既有時段數） |
| **B｜蔡戈尼溶解** | §3 線頭轉終態 | 同路徑 A 輪內，`op == complete/abandon` 之後（設計） | **1** | `DISSOLVE_MAX_PER_DAY = 3`（＝ `ACTIVE_CAP_HARD_MAX`） |

**例外（唯一）**：旗標 LIFE_THREAD_BOOTSTRAP_ENABLED 為 ON 時，另有一條 §4.4 的 bootstrap 路徑（每 agent 每 epoch 恰一次、預設關、缺席即 OFF）。**除上述兩條常態路徑與這一個例外之外，本引擎 0 條 LLM 路徑。**（特別聲明：閘門判定本身、fold、狀態機、SAGE 寫入**皆不呼叫 LLM**；`world_collision_detected` 只讀檔案。）

### 8.2 每 agent 每日最壞情況

| 情境 | 路徑 A | 路徑 B | 合計／agent／日 | 備註 |
|---|---|---|---|---|
| **閘門全 False（典型安靜日）** | **0** | **0** | **0** | 留白合法（VISION §2.3） |
| **僅 morning 到期** | 1 | 0 | **1** | — |
| **早晚各到期** | 2 | 0 | **2** | — |
| **早晚各到期 ＋ 每輪各結掉 1 條線頭** | 2 | 2 | **4** | — |
| **🔴 最壞情況** | **2** | **3** | **5** | 上限由兩個常數**獨立封頂**；路徑 B 的 3 需 3 條線頭同日轉終態（而活躍上限為 3） |

### 8.3 規模化（×10 agents，`configs/default.yaml:11-92` 共 10 個啟用 agent）

| 情境 | 每 agent／日 | ×10 agents／日 | ×10 agents／30 日 |
|---|---|---|---|
| 典型安靜 | 0 | **0** | **0** |
| 典型（早晚各 1 次喚醒、0.2 次溶解） | ≈2.2 | **≈22** | ≈660 |
| 上界（早晚各到期 ＋ 各結 1 條） | 4 | **40** | 1,200 |
| **🔴 最壞情況** | **5** | **50** | **1,500** |

### 8.4 上限如何被閘門壓住（逐項）

1. **時段閘門**：只有 `morning`(08:00)／`night`(22:00) 兩個評估點（`src/soul/scheduler.py:71-72`）→ 路徑 A 每日 ≤2，且 `_slot_for_time` 的 ±60s 視窗 ＋ `_last_trigger_date` 同日去重（`:1810-1811`）保證**不會重複觸發**。
2. **二元短路**：兩布林皆 False 時**預設完全跳過 LLM**（INV-3）；旗標 ON 時額外允許 §4.4 的一次 bootstrap ⇒ 真實世界的多數時段成本仍為 **0**。
3. **容量上限**：`active <= 3` → 可轉終態的線頭數 ≤3 → 路徑 B 每日 ≤3。
4. **計數節流**：`LIFE_THREAD_WAKE_MAX_PER_DAY` / `DISSOLVE_MAX_PER_DAY` 為 **per-agent/per-day 計數器**，超限即**不呼叫**（fail-closed 至「安靜」）。
5. **與既有成本閘門疊加（不互相取代）**：路徑 A/B 的產出若要變成對外訊息，仍須再穿過既有 G3/G4/G7/G7b 與 `_inner_life_gate_check`（§6.3 末段）→ **每日對 Bryan 的主動訊息上限仍為 1 則**（`src/soul/proactive_policy.py:30`），**不因本引擎提高**。

> **成本對照（治理用）**：本引擎最壞 +5 呼叫/agent/日，而**既有**每日固定 LLM 成本包含 diary morning/night（2，`src/soul/scheduler.py:1658-1660`）、dream（≤1，`:1662-1663`）、event（`random.choice` 抽樣，`:1665-1666`）、shared_event／cross_chat（`:1668-1672`）等。**本引擎屬同量級，非數量級躍增**；且**安靜時為 0**。

---

## §9 驗收觀測點（生產中「怎麼看出它活了」）

沿用既有 log 前綴慣例（`[Scheduler]`／`[LifeThread]`／`[SM-3 Decision]` 等方括號前綴），**每一項都可用既有 log 檔 grep 驗證**。

| # | 模組 | 觀測證據（log 行／欄位／計數） | 怎麼讀 |
|---|---|---|---|
| **O1** | 模組 3 閘門 | `[LifeThread] gate quiet agent=<id> slot=<morning\|night> due=False collision=False` | 出現即證明**閘門活著且在留白**（0 LLM）。**這是最重要的「健康」證據**——安靜是有紀錄的選擇，不是死掉 |
| **O2** | 模組 3 閘門 | `[LifeThread] gate wake agent=<id> slot=<slot> due=<bool> collision=<bool>` | 出現即證明放行；`due`/`collision` 兩布林值可事後核對判定理據 |
| **O3** | 模組 3 閘門 | `[LifeThread] gate skip agent=<id> reason=budget` | 出現即證明每日預算封頂生效 |
| **O4** | 模組 4 起源 | `[LifeThread] created agent=<id> thread=<uuid8> origin=<origin_type> target=<share_target>` | 逐 `origin_type` 計數 → 可驗收 §5.3 的起源多樣性 |
| **O5** | 模組 1 狀態機 | `[LifeThread] transition agent=<id> thread=<uuid8> active→dormant` | 狀態機真的在動；非法轉移則為 `[LifeThread] 拒絕非法轉移 …` |
| **O6** | 模組 1 容量 | `[LifeThread] cap agent=<id> active=<n> cap=<k> (create rejected)` | 容量防線真的在擋（**防 Goals v2 的直接證據**） |
| **O7** | 模組 2 溶解 | `[LifeThread] dissolved agent=<id> thread=<uuid8> status=<completed\|abandoned> fact_id=<id\|EMPTY>` | `fact_id` 非 `EMPTY` = SAGE 寫入成功；`EMPTY` = 失敗待重試 |
| **O8** | 模組 2 溶解 | `[LifeThread] dissolve skip agent=<id> thread=<uuid8> reason=already_dissolved\|budget` | 冪等與預算生效 |
| **O9** | 模組 5 錨 A | prompt 內出現 `- [<YYYY-MM-DD> thread] ` 行 | 以既有 prompt 觀測通道取樣；**證明錨 A 供給源已從 diary 擴展到線頭** |
| **O10** | 模組 5 退役 | `[Scheduler] 🤝 shared_event 觸發: … 一起 <activity>`（既有行，`src/soul/scheduler.py:1039-1042`）＋ 新比對行 | P1 階段可比對「線頭活動」與「抽籤活動」是否一致 |
| **O11** | 全域 | `life_threads.jsonl` 列數成長率（`wc -l` 每日快照） | 健康形態：**有增有終**（終態列持續出現）。**只增不終 = 膨脹警訊**；長期 0 列 = 合法留白（**不是**失敗，見 INV-7） |
| **O12** | 全域 | fold 後 `active` 計數分佈（per agent） | 落在 **0~3** 且常態 **1~3**（§2.6.3） |

---

## §10 分階段實作順序

### 10.1 模組依賴圖

```
                    ┌─────────────────────────┐
                    │ M1 資料模型＋存儲        │  ← 無依賴，必須最先
                    │ (life_threads.jsonl)    │
                    └───────────┬─────────────┘
                                │ (被所有模組讀)
              ┌─────────────────┼─────────────────┐
              ▼                 ▼                 ▼
   ┌──────────────────┐ ┌──────────────┐ ┌──────────────────┐
   │ M3 閘門 Salience │ │ M4 起源注入  │ │ M2 蔡戈尼溶解    │
   │ (只讀 M1)        │ │ (寫 M1)      │ │ (讀 M1→寫 SAGE)  │
   └────────┬─────────┘ └──────┬───────┘ └────────┬─────────┘
            │                  │                  │
            └──────────────────┼──────────────────┘
                               ▼
                    ┌─────────────────────────┐
                    │ M5 介接（ACTIVITY_POOL   │  ← 最後
                    │ 退役 + 錨 A 注入）       │
                    └─────────────────────────┘
```

**依賴關係（硬性）**：
- **M1 是所有人的前置**（沒有檔案格式就沒有可讀寫的東西）。
- **M2 依賴 M1 的終態語意**（§2.5 狀態機必須先凍結）。
- **M3 只讀 M1**，可與 M4/M2 平行。
- **M5 依賴 M1+M4**（要有線頭內容才談得上注入錨 A；要能讀活躍線頭才談得上退役 `ACTIVITY_POOL`）。

### 10.2 建議的最小先落地單元（**M1 先做，且只做 M1**）

**建議：第一張實作票 ＝ M1（資料模型 ＋ 存儲 ＋ 狀態機 ＋ 容量防線），不含任何 LLM 呼叫。**

**理由（逐條）**：
1. **M1 是唯一「0 LLM 成本」的模組** → 可以在**生產**先落地並長期觀察，**不增加一分錢**（Owner 對成本敏感）。
2. **M1 可完全以測試驅動**：fold 規則、append-only、狀態機封閉性、容量上限、per-agent 隔離（INV-1/4/5/6/10）全部是**純函式／純 IO 斷言**，不依賴 LLM 品質。
3. **M1 落地即產生可觀測的真實資料**（O5/O6/O11/O12），使**後續 M3 的閘門可以在真資料上調參**，而不是在猜測上設計。
4. **風險最低**：M1 不觸碰任何既有模組（0 介接），**可獨立回滾**（刪檔、移除外掛模組）。
5. **避免「先做閘門」的死結**：若先做 M3 而無 M1，閘門永遠面對空檔 → 恆 False → **看起來像死鎖**（正是本專案一直在踩的坑）。

**後續順序建議**：M1 → M4（起源注入，讓檔案開始有真實內容）→ M3（閘門，有內容才驗得動）→ M2（溶解，需先有終態線頭）→ M5（介接，最後且分 P1/P2/P3 三階段）。

> ⚠️ **M2 排在 M4 之後的理由**：溶解的驗收需要「真的結掉的線頭」為樣本；先做 M2 會無料可測。

---

## §11 Open Questions（需 Owner／主大腦裁定）

> 未裁定前，各項以「**維持現狀／採本契約建議值**」為預設。**不以猜測填空。**

| # | Open Question | 為什麼需要裁定 | 本契約的建議 ＋ 理由 | 未裁定時的預設 |
|---|---|---|---|---|
| **OQ-1** | 🔴 **「五大起源」vs 4 個 `origin_type` 的落差如何處置？** | 工單骨架 §5 標題寫「五大起源」，資料模型只列 4 個 `origin_type`；工單明令「**不得自行發明第五個**」，須交裁定 | **建議：維持 4 個 `origin_type`，不新增第五源。** 理由（L1 依據，非推測）：**VISION §4.3「起源多樣性（五大驅動）」的第 5 項逐字是「零線頭（合法留白）」——它不是一個 `origin_type`，而是一個不落盤的終態缺席**（零線頭＝沒有線頭存在，**無物可落盤**）。故「五大驅動」＝ 4 個可落盤起源 ＋ 1 個合法留白態，**與資料模型完全一致，落差已由 VISION 自身消解**。<br>工單另建議的候選（關係軸／記憶軸）**建議不採**：(a) **關係軸**已有專屬子系統（`relational_band`＋`impression_tags`＋TA-2），再開一個 `origin_type` 會製造**第二個關係真相來源**（違反 §1.2「唯一真相來源」）；(b) **記憶軸**是**消費端**（SAGE 是溶解的**去處**，不是起源），設為起源會造成**循環依賴**（線頭→SAGE→線頭）。<br>另註：VISION §3 另有**一組不同的「五條線」**（活動／關係／想法／目標／體感），那是**基質的投影分類**，**不是** `origin_type` 值域，兩者不可混用 | 4 個 `origin_type`（照本契約 §2.3） |
| **OQ-2** | 🔴 **是否補齊日間時段 checkpoint（noon／afternoon／evening）？** | 工單骨架假設有 5 個 checkpoint，**實證只有 morning/night 兩個**（§4.1 E3/E4）。補齊＝**新增時段常數與觸發點**，屬新排程面；不補＝角色白天只有 `event`（4–8h 隨機）可動 | **建議：本契約（第一版）不補，維持 morning/night 兩評估點。** 理由：(a) 工單同時要求「**0 新增定時器**」，新增時段最容易被實作成新 timer；(b) 先以 2 點/日驗證閘門經濟性，**成本可控**（§8.2 最壞 5 呼叫/agent/日）；(c) 若日間真的需要，**正確做法是新增 slot 常數走既有 `_slot_for_time`**（`src/soul/scheduler.py:806-815`）而非新 timer——但此變更會**改變 `LIFE_THREAD_WAKE_MAX_PER_DAY`**（§4.1），須同步重算成本表 | 不補（2 個評估點） |
| **OQ-3** | **SAGE 寫入的 `origin` 應取 `lived_experience` 還是 `native_episode`？** | `Fact` 模型**不存在** `Episodic` kind（§3.4）；`origin` 5 值中兩者語意相近，選錯會影響 EH 認知地平線的檢索語意 | **建議：`lived_experience`。** 理由：該值是 v9 schema 對「未標記」的 DB DEFAULT，語意＝親身經歷；`native_episode` 在 EH-1 契約語境偏向「原生情節片段」，與生活線頭（長張力）語意不同。**兩者皆不走認知三態**（`src/memory/sage/models.py:49-51` 僅 `assimilated`/`external_world` 走三態），故**不影響 horizon 過濾**——這是選哪個都安全的原因 | `lived_experience` |
| **OQ-4** | **`world_collision_detected` 用 `accepted` 還是更嚴格的 `context_injected`？4h 與既有 24h novelty 視窗的關係？** | `accepted`（過 0.35 感知門檻）與 `context_injected`（真的進了某 agent 的 prompt）語意不同；且既有 novelty 視窗是 **24h**（`src/world/state.py:73`），本契約新增 **4h** | **建議：用 `accepted`，保留 4h。** 理由：`context_injected` 要求「當時剛好有 agent 在做回合」，會讓閘門**被無關時序綁架**（夜間無回合＝永遠 False＝白天也醒不來）；`accepted` 是**管線自身**對「這是真實新世界事實」的判定，語意與「世界碰撞」直接對齊。4h 是**工單指定**，且比既有 24h **更嚴**（不會放寬），兩者並存不衝突：24h 管「感知狀態保鮮」，4h 管「這一時段值不值得醒」。**若 Owner 要更保守，改 `context_injected` 即可（單一常數切換）** | `accepted` ＋ 4h |
| **OQ-5** | 🔴 **SI-2.1 三道防線在生產未接線，是否納入本引擎的前置條件？**（**既有風險，非本契約引入**） | L2 實證：**(1)** `IdentityFirewall`（防線 3，`src/social/identity_firewall.py:43`）**生產未注入**——`scripts/run_server.py:495-498` 建構 `SubmissionGate` 時無 `identity_firewall=`／無 `agent_id=`，故 `src/inner_life/submission_gate.py:333` 的 `if self._identity_firewall is not None:` 恆跳過；**(2)** `SocialEventProducerGate`（防線 2，`src/social/producer_gate.py:65`）在 `scripts/` **0 次實例化**；**(3)** `SOCIAL_WORLD_EVENT` **有消費端、0 生產端**（消費端 `src/world/middleware.py:352`；wiring `scripts/run_server.py:585`）。**⇒ 若任何設計假設三道防線在生產生效，前提不成立** | **建議：(a) 本引擎不以三道防線為前提**（故 INV-4 改以**路徑分割**自我保證隔離，**不依賴防線 3**）；**(b) 但把「防線 3 未接線」提升為獨立治理項**，請主大腦決定是否另開票接線——**理由**：這是一個**已存在的安全缺口**，不因本引擎而產生，但本引擎的 per-agent 資料會**擴大**受影響面（現有 `data/soul/<agent>/*` 已是 per-agent，故風險增量為**低**，但值得登記）。**本契約不代為裁定是否解凍接線** | (a) 路徑分割自我保證；(b) 缺口留在 §11，另案處理 |
| **OQ-6** | **「心理阻力」判定是否需要比 §6.3 更豐富的模型？** | §6.3 刻意壓到 4 個等值／成員判定。若太嚴，`user_bryan` 可能長期不觸發（錨 A 供給仍低）；若太鬆，違反 VISION §5.1「拒絕扁平平權四選一」與「防止 Bryan 被瑣事轟炸」 | **建議：先照 §6.3，以生產資料觀察再調。** 理由：任何「想念強度／重要程度」的數值化**直接違反** VISION §2.4；若要放寬，**唯一合規方向是擴大離散集合**（例如把 `whim_driven` 在特定條件下納入），**不得**引入分數。**觀察地平線建議 ≥30 日**（需累積足夠終態樣本） | 照 §6.3 |
| **OQ-7** | **`life_threads.jsonl` 是否需要輪替／歸檔？** | 檔為 append-only 單檔，長期只增（雖然終態列會持續出現）。既有 `diary` 是 per-day 分檔；`decision_trace`／`motive_trace` 是**單檔且無輪替**（`src/soul/decision_trace.py` 明文「0 rotation」） | **建議：不輪替**（沿用 `decision_trace`／`motive_trace` 慣例）。理由：單檔成長率極低（最壞 3 建 ＋ 3 終／agent／日 ≈ 6 列/日/agent ≈ 2,200 列/年/agent），遠低於 `perception_trace.jsonl` 的既有量級（審計記載 **11,099 行**）。**若未來需要，另開維護票** | 不輪替 |

---

## §12 Frozen Contract 觸點清單

> **判定規則**：凡本契約設計需要**修改**下列任一凍結面者，逐條列出**最小解凍範圍**與安全邊界。**沒有就明寫「無」。**

**凍結清單**（依工單）：Agency 4 stages／`TriggerEnvelope`／`InnerLifeEvent`／4 handlers／SAGE 寫入邏輯／`SubmissionGate`／SI-2.1 三道防線／`DecisionResult` 形狀／`DECISION_ACTIONS`。（**`AGENT_SPEAK` 不在凍結清單**。）

| # | 凍結面 | 本契約是否需要變更 | 最小解凍範圍（若需） | 安全邊界 |
|---|---|---|---|---|
| F1 | **Agency 4 stages** | **無** | — | 本引擎**不進入** Agency 觸發鏈；§6.2 的錨 A 投影是**在既有 proactive_dm 交付鏈內**補料，**不新增 stage、不新增 trigger_type**（既有 8 個 `VALID_PRODUCER_TRIGGER_TYPES` 不動，`src/inner_life/submission_gate.py:82-91`） |
| F2 | **`TriggerEnvelope`** | **無** | — | **0 欄位新增**。錨 A 走**既有** `draft` 欄位（`src/llm/proxy.py:3564`），不新增 envelope 欄位 |
| F3 | **`InnerLifeEvent`** | **無** | — | 線頭**不是** `InnerLifeEvent`；**不得** create_event（沿用 `src/memory/sage/provider.py:597` 的「Memory 是 consumer，絕不 create_event」原則） |
| F4 | **4 handlers** | **無** | — | **0 新增 handler、0 修改** |
| F5 | **SAGE 寫入邏輯** | **無**（**只呼叫既有公開介面**） | — | 唯一呼叫點 ＝ `MemoryWriter.add_fact(fact) -> str`（`src/memory/sage/writer.py:164`）。**不得**改 `src/memory/sage/**` 任何檔案、**不得**改 `post_reply_commit` 簽名（`src/memory/sage/provider.py:586`）、**不得**新增 schema 欄位、**不得**改變既有 merge 行為（`:164-170`） |
| F6 | **`SubmissionGate`** | **無** | — | **0 修改**。線頭落盤**不經** SubmissionGate（線頭非 `InnerLifeEvent`）；§4.3 已聲明正交 |
| F7 | **SI-2.1 三道防線** | **無** | — | **0 修改、0 移除**。VISION §8 明文「既有外圍安全防線嚴格維持在位，絕不提前拆除」。**特別註明：本契約不要求接線未生效的防線**（見 OQ-5）；INV-4 因此改以路徑分割自我保證 |
| F8 | **`DecisionResult` 形狀** | **無** | — | **6 欄逐字不變**（`src/soul/decision.py:111-126`：`decision`/`transmit`/`reason`/`motive_id`/`motive_content`/`provenance_ref`）。本引擎**不載入、不產生、不消費** `DecisionResult` |
| F9 | **`DECISION_ACTIONS`** | **無** | — | **逐字不變**（`src/soul/decision.py:95`：`("transmit","observe","reflect","do_nothing")`）。本引擎**不新增第五個 action** |

**⇒ 總結：本設計需要 0 項 Frozen Contract 變更。全部 9 個凍結面皆為「無」。**

**另附一項「明確不解凍」聲明**：本契約**不**申請、**不**授權任何 `src/**` 解凍。實作票若需動 `src/**`，**須由主大腦另行申請並經 Owner 核准**（比照 `ELEVATION-FIX-A-1` 的「凍結已由 Owner 授權解除，僅限本票範圍」程序）。

---

## §13 本契約的自我邊界聲明

- 本契約**只讀**程式碼、既有文件與唯讀資料，**唯一產出** `docs/LIFE-THREAD-ENGINE-CONTRACT.md`（＋ `logs/ENGINEERING_STATE.md` 登記）。
- **0 `src/**`、0 `configs/**`、0 `scripts/**`、0 `tests/**` 改動。**（若觸碰 `tests/**` 即違反本票紅線。）
- **0 服務重啟 / 0 kill**：未執行 `server_ops`、watchdog、Plan A；未讀取或修改 `scripts/_watchdog.ps1`。
- **0 生產 `data/**` 寫入**；未開啟 `data/faulthandler.log`；0 log 清理／輪替。
- **0 Frozen Contract 變更**（§12：九項全「無」）。
- **未使用 `git add -A` / `git add .`**（逐檔明確 add）。
- 所有 `path:line` 皆由起草者**親自以 `read`/`grep` 取得**；量不到者標 **NOT FOUND** 或列入 §11 OQ，**不以推測填空**。
- **本契約不自行生效**：只作為未來實作工單的規格依據；實作前需主大腦 ＋ Owner 確認。

---

## 附錄 A：介接點證據表（**逐項 `path:line`**）

> 全部由起草者親自讀取。行號以起始 HEAD `dfa7f07` 為基線。

### A.1 儲存與資料模型（模組 1）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| 既有 per-agent diary 落盤（路徑組法＋append 寫入） | `src/soul/dream_event.py:480-512` | `:481` 組 `diary/YYYY-MM-DD.jsonl`；`:503` `open("a")` 單次 `write`＋`\n`；`:484-489` entry 4 必填鍵 |
| 既有 diary entry schema | `src/soul/dream_event.py:484-499` | `ts`/`slot`/`content`/`source`（＋選用 `inner_life_event_id`/`activity`/`category`/`shareable`） |
| 既有 append-only 變更日誌先例（同 id 多列） | `src/soul/motive.py:278-448` | `MotiveTraceStore`；生產實測 47 列 / 24 distinct id |
| 既有 append-only + 永不 raise 先例 | `src/soul/decision_trace.py:88-99` | `DecisionTraceStore.append`；10 欄凍結 schema |
| 壞 JSON 行跳過（不 crash）先例 | `src/llm/proxy.py:288-291` | `except json.JSONDecodeError: continue` |
| `data_root()` 路徑慣例 | `src/world/trace.py:36-38` | `from src.paths import data_root` → `data_root() / ...` |
| per-agent 儲存目錄實況 | `data/soul/agent_ruka/`（`diary/` ＋ `relationships.json`） | 已存在 per-agent 目錄慣例 |
| 全域（非 per-agent）jsonl 對照組 | `data/soul/{decision_trace,motive_trace,interactions}.jsonl` | 三者為全域單檔 |

### A.2 SAGE／記憶（模組 2）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| **SAGE 寫入公開介面（唯一呼叫點）** | `src/memory/sage/writer.py:164` | `def add_fact(self, fact: Fact) -> str`；`written[0]` → `merged[0]` → `""` |
| 批次寫入 | `src/memory/sage/writer.py:172-173` | `add_facts_batch` |
| 帶確認寫入 | `src/memory/sage/writer.py:175-178` | `write_with_confirmation` → `WriteResult` |
| graph store 層 `add_fact` | `src/memory/sage/graph_store.py:382` | 實際落庫點（**不改**） |
| turn-based 寫入（**不得用於溶解**） | `src/memory/sage/provider.py:586-601` | `post_reply_commit(session_id,last_user_msg,agent_reply,source_pair,inner_life_event_id)` |
| `Fact` 模型全欄 | `src/memory/sage/models.py:7-56` | 20 欄（`@dataclass` 欄位 20 個；`to_dict()` `:59-80` 同為 20 鍵）；`source` 值域 `{user,inference,correction}` `:16` |
| `Fact.origin` 5 值 | `src/memory/sage/models.py:45-56` | `native_commons`/`native_episode`/`lived_experience`/`assimilated`/`external_world`；僅後二者走三態 |
| merge／去重痕跡欄 | `src/memory/sage/models.py:20-21` | `merged_from` / `merge_reason` |

### A.3 排程與閘門（模組 3）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| **兩個時段常數** | `src/soul/scheduler.py:71-72` | `morning` 08:00／`night` 22:00 |
| slot 值域註解 | `src/soul/scheduler.py:96` | `# slot ∈ {"morning", "night"}` |
| slot 映射（3 份副本） | `src/soul/scheduler.py:797`, `:809`, `:1770` | 逐字 `[("morning",…),("night",…)]` |
| slot 判定視窗 | `src/soul/scheduler.py:806-815` | `if 0 <= diff < 60: return slot` |
| **主迴圈（tick 本體）** | `src/soul/scheduler.py:1650-1702` | `:1654` while；`:1696`/`:1702` `await asyncio.sleep(30)`（**裸字面量，無常數**） |
| tick 分支清單 | `src/soul/scheduler.py:1657-1687` | slot／dream／event／shared_event／cross_chat／heartbeat／proactive_dm／`_goal_scan_all`／`_fire_periodic_narrative` |
| **建議掛載點** | `src/soul/scheduler.py:1687` 之後新增一行（設計） | 照抄 `_fire_periodic_narrative` 形態 |
| **時段守門先例（要照抄）** | `src/soul/scheduler.py:1753` | `if self._slot_for_time(now) != "night": return` |
| 同日去重 | `src/soul/scheduler.py:1810-1811` | `if self._last_trigger_date.get(key) == today: continue` |
| 無條件每 tick 檢查先例＋24h due 節流 | `src/soul/scheduler.py:1681` → `src/goals/seed_provider.py:222` | `_goal_scan_all` → `if st - state.last_seed_scan_at < GOAL_QUOTA_WINDOW_SECONDS: return []` |
| recency／due 布林先例 | `src/agency/inner_life_gate.py:350`（門檻 `:129`） | `if elapsed < min_interval_minutes:`（30 分） |
| 既有 inner-life gate（**正交，不改**） | `src/soul/scheduler.py:340`；唯一呼叫 `:291-292` | 僅 `proactive_dm`；例外時 fail-open `:401-407` |
| `SubmissionGate`（**正交，不改**） | `src/inner_life/submission_gate.py:180` | 無數值門檻；firewall 步驟 `:333-345` |
| timer 清單（既有，**不新增**） | `src/heartbeat/engine.py:164-166`（60s）等 | 另有 `scripts/run_server.py:394`（600s）、`:1682`（15s）、`:783`（300s）、`:849`（1800s）、`:933`（1800s） |
| **APScheduler／threading.Timer／call_later** | 全 repo | **NOT FOUND（0 命中）** |

### A.4 世界／Lived Context（模組 4）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| **世界感知唯一落盤產物** | `src/world/trace.py:36-38`, `:52-53` | `data_root()/world/perception_trace.jsonl`，append |
| `WorldPerceptionTrace` schema | `src/world/perception.py:297-334` | `event_id`/`timestamp`/`source`/`event_type`/`scores`/`accepted`/`context_injected`/`memory_written`/`novelty_id`/`selection_reason`/`extra`／**`extra.summary`**（**修訂 1**：僅 `accepted == True`、≤ 200 字元、空／純空白不寫鍵；**缺欄表示無 fact text**） |
| **fact text 落點**（既有 artifact，**加法**） | `src/world/middleware.py:581-607`（外部事件 `phase=evaluated`）、`:776-803`（社交事件 `phase=social_evaluated`） | 寫進**既有** `extra` 的 `summary` 鍵；僅 `accepted == True`，值經 `strip()` 後截斷 ≤ 200 字元；空／純空白 ⇒ **不寫該鍵**（實作 `_fact_summary_extra()`，`src/world/middleware.py:97-117`） |
| fact text 上限常數 | `src/world/middleware.py:94` | `FACT_SUMMARY_MAX_CHARS = 200`（與讀取端 `src/soul/life_thread_origins.py:103` `MAX_FACT_CHARS` 同值） |
| **原始 WorldEvent 不落盤** | `src/world/state.py:7-8`, `:16-21`, `:86` | 不變量 `EPHEMERAL`／`NO PERSISTENCE`；deque in-memory |
| 既有 novelty 視窗 24h | `src/world/state.py:73`（執行 `:171`）；`src/world/middleware.py:218` | `timedelta(hours=24)` |
| 新聞 lookback 2h | `src/world/source/news_rss.py:167`（用於 `:606`） | `DEFAULT_LOOKBACK_HOURS = 2` |
| 日曆 lookahead 24h ＋ grace 1h | `src/world/source/calendar_ical.py:96`, `:105` | Bryan 日程來源 |
| 天氣來源 | `src/world/source/open_meteo.py:131`, `:579-610` | `rain_started`／`weather_temp_change`；poll 1800s |
| 世界感知注入 prompt（private） | `src/llm/proxy.py:1383-1384` | `if world_context and world_context.strip(): system_parts.append(...)` |
| 世界感知注入 prompt（group） | `src/llm/proxy.py:735-736` | 同構 |
| 世界感知文本與標記 | `src/world/perception.py:270-278` | 標記 `[世界感知]`；`:277` bullet **不渲染時間戳** |
| payload 鍵 | `src/world/middleware.py:642`（組裝 `:623`） | `"world_context": world_context_text` |
| 感知門檻 | `src/world/perception.py:578` | `DEFAULT_ACCEPT_THRESHOLD = 0.35` |
| 感知 top-N 預算 | `src/world/middleware.py:80` | `DEFAULT_PERCEPTION_BUDGET = 3` |
| 世界事件白名單 | `src/world/inner_life_adapter.py:121-128` | `WORLD_QUALIFYING_TYPES` |

> **界線說明（A.4／修訂 1, WORLD-FACT-TEXT-PERSIST-1, 2026-09-14）**：本表「**原始 WorldEvent 不落盤**」一列**仍然成立**——落盤的**只有** `WorldPerceptionTrace`（**既有** schema；本次僅 `extra` 多一個鍵）。`src/world/state.py:7-8`, `:16-21` 的 `EPHEMERAL`／`NO PERSISTENCE`／`NOT a memory system` 不變量**範圍不變**（它規範 **world state**，不規範 trace）；世界層**仍不持久化 world state、也不回讀**。fact text 是**事實文字副本**，不是世界狀態的復原依據。

### A.5 交付鏈與錨 A（模組 5）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| 決策入口 | `src/soul/scheduler.py:300`／def `:409` | `_decision_check`（`:300` 為唯一呼叫點，見 `:1508` 註解；`§6.2.1` 同值） |
| 決策分流 | `src/soul/scheduler.py:483-502` | `if result.transmit:` → `mark_transmitted` → `return True` |
| 觸發發布 | def `src/soul/scheduler.py:254`（proactive_dm 呼叫點 `:1626`） | `_publish_agency_trigger` |
| Agency handler | `src/agency/trigger_handler.py:84` → `:132` | 過 4 stages 後呼叫 `llm_executor` |
| 生成入口（draft 組裝） | `scripts/run_server.py:1174`（draft `:1213-1219`；`:1276` `_fire_intent`） | `_proactive_dm_llm_executor` |
| draft → `user_message` | `src/llm/proxy.py:3564`；`:3592-3599` | `user_message = draft`（唯一承載發訊理由） |
| **🔴 錨 A 注入點（private）** | **`src/llm/proxy.py:1353-1360`** | `[最近內在生活]` 區塊；供給 `_format_recent_inner_life` |
| **🔴 錨 A 注入點（group）** | **`src/llm/proxy.py:719`** | 同一 helper |
| 錨 A 供給函式 | `src/llm/proxy.py:250-312` | 讀 diary 近 3 日；`:293` slot 白名單；`:301` `source=="llm"`；`:309` 行格式；`:312` 取末 5 行 |
| 錨 A 常數 | `src/llm/proxy.py:134-137` | `INNER_LIFE_DATA_DIR`／`DAYS=3`／`MAX_ENTRIES=5`／`MAX_CHARS_PER_ENTRY=60` |
| 錨 C（關係感知，**已存在**） | `src/llm/proxy.py:1341-1346` → `:509-571` | 條件式（`motive_target` 非空才注入） |
| legacy relationship block | `src/llm/proxy.py:1331-1333`（門檻 `:349-352`） | M5.13-3 舊口徑 |
| 送出 | `src/io/channels/router.py:168` | `_on_agent_speak` |
| **M7-1 `ACTIVITY_POOL` 定義** | **`src/soul/dream_event.py:220-231`** | 10 項 dict；全 agent 共用；無權重 |
| **`ACTIVITY_POOL` 消費者 1** | `src/soul/dream_event.py:669` | `write_event` 內 `random.choice` |
| **`ACTIVITY_POOL` 消費者 2** | `src/soul/scheduler.py:1032-1034` | `_fire_shared_event` 內 `random.choice` |
| 產物下游客（活動→傳訊） | `src/soul/scheduler.py:1215`（過濾 `:1260-1264`；呼叫 `:1607`；`extra` `:1609`；publish `:1626`） | `_get_recent_shareable_activity` |
| 產物下游客 2（觀測面） | `src/io/gateway.py:516`, `:532` | `/api/soul/status` 的 `recent_activity` |
| M7-1 測試 | `tests/test_m7_1_activity_model.py:18`, `:58`, `:61-71`, `:104-106` | `assert len(ACTIVITY_POOL) == 10` 等 |

### A.6 Frozen／身份（§12 依據）

| 介接點 | 檔案:行 | 現況一句話 |
|---|---|---|
| `DECISION_ACTIONS` | `src/soul/decision.py:95` | `("transmit","observe","reflect","do_nothing")` |
| `DecisionResult`（6 欄） | `src/soul/decision.py:111-126` | `@dataclass(frozen=True)` |
| `FAIL_CLOSED_REASON` | `src/soul/decision.py:98` | `"decision_llm_failure_or_bad_output"` |
| `IdentityFirewall` | `src/social/identity_firewall.py:43`（不變量 `:9-16`；判定 `:35-40`） | 防線 3 實作**存在** |
| 🔴 防線 3 **生產未接線** | `scripts/run_server.py:495-498` ＋ `src/inner_life/submission_gate.py:333` | 無 `identity_firewall=` → `if ... is not None` 恆跳過 |
| 防線 2 生產未實例化 | `src/social/producer_gate.py:65` | `scripts/` 0 次實例化 |
| 防線 1 有消費端、0 生產端 | 消費 `src/world/middleware.py:352`；wiring `scripts/run_server.py:585` | `SOCIAL_WORLD_EVENT` 0 publish |
| 每日主動 DM 上限（**不變**） | `src/soul/proactive_policy.py:30`, `:33-45` | `PROACTIVE_DM_DAILY_CAP_PER_AGENT = 1`；純函式 |
| G3 冷卻／G4 靜音／G7 門檻／G7b 上限 | `src/soul/scheduler.py:155`／`:156-157`,`:1166`,`:1484`／`:87`,`:1565`／`:1589`,`:1390` | 既有外圍防線全在位 |
| 人格載入器（**「SOUL.md」的真身**） | `src/llm/proxy.py:2416`, `:1926`, `:2461`（fallback `:1905-1908`） | `personas/agent_<short>.md`，H1 自稱 `# SOUL.md - …` |
| **磁碟上無 `SOUL.md` 檔** | `personas/`（10 檔 `agent_*.md`） | **NOT FOUND**（任何名為 `SOUL.md` 的檔案） |
| per-agent 可宣告面（容量規則落點） | `configs/default.yaml:11-92` | `agents[]` 既有鍵：`id`/`class`/`intimacy_level`/`enabled` |

---

## 附錄 B：Out of Scope（本契約明確不做）

1. ❌ **不實作任何程式碼**（0 `src/**`／0 `configs/**`／0 `scripts/**`／0 `tests/**`）。
2. ❌ **不重啟、不 kill、不執行 server_ops／watchdog／Plan A**。
3. ❌ **不寫入生產 `data/**`**；不開啟 `data/faulthandler.log`；不清理／輪替 log。
4. ❌ **不改任何 Frozen Contract**（§12 九項全「無」）；不申請 `src/**` 解凍。
5. ❌ **不發明第五個 `origin_type`**（OQ-1 交裁定）。
6. ❌ **不發明不存在的時段 checkpoint**（`noon`/`afternoon`/`evening` 實證不存在；OQ-2 交裁定）。
7. ❌ **不引入任何數值打分**（0 浮點評分欄位、0 權重、0 排序分）。
8. ❌ **不新增任何定時器／tick／sleep／背景輪詢**。
9. ❌ **不新增投遞通道、不新增 trigger_type、不新增 handler**。
10. ❌ **不改關係帶／TA-2／配額／閘門**（Owner 2026-09-14 裁定：下游微調全面停止）。
11. ❌ **不碰 `scripts/_watchdog.ps1`**（紅線；且本契約起草過程未讀取、未修改）。
12. ❌ **不代 Owner 裁定 §11 任一 OQ**。
