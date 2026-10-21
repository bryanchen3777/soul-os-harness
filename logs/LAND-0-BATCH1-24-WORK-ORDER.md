# 工單：LAND-0-BATCH1-24 — 24 隻含陰影層角色落地（第一批）

> **狀態**：決策完備，待派工（**必須等戰役 A 合入後才可動工**，見 §0.3）
> **日期**：2026-10-21 18:35 EDT
> **Owner 裁決**：2026-10-21 18:31 —— 採納 Notion 版本（接受段落合併版，禁止回溯對話考古）；依賴序列＝戰役 A 驗收合入 → 本票 → 22:00 槽被動巡檢
> **權威規格**：`logs/LAND-0-WORK-ORDER.md`（Notion SSOT 工單 LAND-0 的 repo 快照，含 36 筆 speaker_token 數值表、類別註冊表、display-name／short-name 對照表）
> **素材**：已在 `docs/persona-drafts/`（36 份，666,931 bytes，commit `a32a90b`）

---

## 0. 主大腦的執行勘查（已實測，執行者不必重做）

### 0.1 素材已就位且已分流

主大腦已從 Notion SSOT 抓齊 36 份素材、套用 LAND-0 規定的兩處落地前改動（首行標題 ＋ 開頭 Status 區塊），並**在抓取當下**施加單檔門閘：

| 批次 | 數量 | 陰影條款計數 | 處置 |
|---|---|---|---|
| **batch1**（Notion 標題「SOUL.md **新建檔草稿**」） | **24 隻** | **恰 1 個** | ✅ **本票落地對象** |
| **batch2**（Notion 標題「SOUL.md **預寫草稿**」） | 12 隻 | 0 個 | ❌ **本票不得落地**（待 SHADOW-0 補齊陰影層） |

batch2 的 12 隻（與 LAND-0 逐字一致）：`beatrice`／`chizuru`／`emilia`／`ichika`／`itsuki`／`mami`／`nagisa`／`nino`／`rika`／`saki`／`shino`／`yotsuba`。

### 0.2 品質裁決已定（Owner 2026-10-21 18:31）

**採納 Notion 合併版逐字落地。** Notion 單頁 100 區塊上限造成的 `###`→段落壓縮**不視為內容損失、不回溯對話考古**；`•` 內聯分隔在 LLM 語義吸收上等價；排版美化另立 LINT-CHORE 票，**不得作為本票的阻斷障礙**。

### 0.3 🔴 硬性先決條件：`src/llm/proxy.py` 衝突

本票要改 `src/llm/proxy.py`（display-name／short-name／`_AGENT_IDENTITY_RULES` 各 +24），
而**戰役 A（SIM-EPIS-PIGGYBACK-1）也在改同一個檔案**。
⇒ **本票必須在戰役 A 的 proxy.py 改動已合入 `main` 之後才可動工**（以已合入版本為基準做增量修改）。**不得**同時改同一檔。

### 0.4 🔴 一處必須由主大腦裁決的耦合點：`NO_DIARY_AGENTS`

LAND-0 §做法指定：`src/memory/sage/provider.py` 的 `NO_DIARY_AGENTS` 由 `{"agent_ram"}` 擴為 `{"agent_ram","agent_beatrice","agent_nino","agent_mami","agent_saki"}`。
**但那 4 隻全在 batch2（擱置名單）** ⇒ 在本票加入會產生**指向未註冊 agent_id 的懸空引用**。
⇒ **主大腦裁定：本票不動 `NO_DIARY_AGENTS`（維持 `{"agent_ram"}` 原狀）**；該 4 筆延至第二批落地時一次加上。**本票不得提前加入。**

---

## 1. 目標

把 **24 隻含陰影層角色**（batch1）完整落地並完成五個註冊面的註冊，使全庫註冊數由 **10 → 34**（10 既有 ＋ 24 本批；batch2 的 12 隻留待 SHADOW-0 之後）。
**本票不部署、不重啟** ⇒ 執行中的生產服務**零行為變化**（註冊面皆於啟動時載入）。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `personas/agent_<id>.md`（**24 份新建**） | 由 `docs/persona-drafts/` **逐位元複製**（見 §3.1） |
| 2 | `src/agent/consciousness.py` | **+24 個子類**（比照 `AgentMiku`／`AgentAoi` 模式） |
| 3 | `src/agent/registry.py` | `AGENT_CLASS_MAP` **+24 行** |
| 4 | `configs/default.yaml` | `agents:` **+24 條**（形狀比照 `agent_miku` 條目） |
| 5 | `src/agent/speaker_token.py` | `BASE_SCORES` **+24 條**（值取 LAND-0 表） |
| 6 | `src/llm/proxy.py` | display-name map／short-name map／`_AGENT_IDENTITY_RULES` **各 +24 條**（**以戰役 A 已合入版本為基準**） |
| 7 | `tests/agent/test_agent_registry_land0_batch1.py`（**新增**） | 註冊面一致性守衛 ＋ 變異實驗 |
| 8 | `logs/LAND-0-BATCH1-IMPLEMENTATION.md`（**新增**） | 實作紀錄（若未授予 `logs/**` 寫入權則在回報中全文列出） |

**不得動**：`src/memory/sage/provider.py`（§0.4）、`docs/persona-drafts/**`（唯讀素材）、`data/**`、`personas/agent_yua.md` 等既有 10 份、既有 10 隻的任何註冊條目、frozen contracts（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／`src/inner_life/submission_gate.py`／`src/social/identity_firewall.py`／`src/social/producer_gate.py`／`src/world/middleware.py`／`src/memory/sage/writer.py`／`src/soul/decision.py`）。

## 3. 做法（決策已定，執行者照做）

### 3.1 `personas/agent_<id>.md`（24 份）

**逐位元複製** `docs/persona-drafts/agent_<id>.md` → `personas/agent_<id>.md`。
🔴 **主大腦已在素材階段套好 LAND-0 規定的兩處改動**（首行 `# SOUL.md — <中文名> (<羅馬字>)` ＋ 開頭 `**Status:** Owner-Approved 2026-10-04 / Canon-Aligned / Shadow Clauses v1（SHADOW-0）`）。
⇒ **執行者不得再改任何一字**；落地後 `personas/agent_<id>.md` 與 `docs/persona-drafts/agent_<id>.md` 必須**位元級完全相同**（`diff` 為空）⇒ 這是本票最強的驗收條件。
⇒ **只能落地 batch1 那 24 隻。**落地前逐檔自我檢查：`grep -c '^## 陰影條款' docs/persona-drafts/agent_<id>.md` 必須 **＝ 1**；**＝ 0 的一律不得落地**（若索引有誤，報回主大腦，不得自行放寬）。

### 3.2 `src/agent/consciousness.py`（+24 子類）

- 比照 `AgentMiku`／`AgentAoi` **最新模式**：**僅**差異化 ① `agent_id` ② persona 檔路徑 ③ diary 政策 ④ 該稿「特別注意(不可違反)」的**專屬禁止句**。
- **不新增框架碼、不改基底類別**（比照 `AgentMiku`／`AgentAoi` 逐字比對其差異面）。
- 類別名照 LAND-0 的類別註冊表（`logs/LAND-0-WORK-ORDER.md`）；**不得自創類別名**。
- 每個子類的專屬禁止句取自**該角色的素材檔**「特別注意(不可違反)」段（**逐字照錄**，不得改寫或摘要）。

### 3.3 `src/agent/registry.py`（+24 行）

- `AGENT_CLASS_MAP` 逐行加上；**註解格式比照既有**（`Phase` 註記改為**批次**註記，例：`# Batch 1 — 小日向彩羽（いもウザ）`）。
- **既有 10 行不動**。

### 3.4 `configs/default.yaml`（+24 條）

- `agents:` 加 24 條，**形狀比照 `agent_miku` 條目**（`- id: agent_…` ＋ persona 來源註解）。
- `initialization_mode` **一律缺省**（＝`seeded`，fail-closed）。**既有 10 條不動。**

### 3.5 `src/agent/speaker_token.py`（+24 條 `BASE_SCORES`）

- 值**逐字取自 LAND-0 的 36 筆數值表**（`logs/LAND-0-WORK-ORDER.md`）—— 該表已含 24 隻 batch1 與 12 隻 batch2 的完整 `(base, jitter)`。
- **只加 batch1 的 24 條**（值不得自行計算或調整）。
- 每條附**一行依據註解**，風格比照既有。

### 3.6 `src/llm/proxy.py`（三張表各 +24 條）

- **display-name map**、**short-name map**、**_AGENT_IDENTITY_RULES** 各加 24 條。
- 內容（中文名／羅馬字／身份句）**逐字取自 LAND-0 規格**與各角色素材（**不得自創**）。
- 🔴 **以戰役 A 已合入的 `proxy.py` 為基準**做增量修改（§0.3）；**不得**覆蓋戰役 A 的接線（`system_parts` 組裝、recall 區塊、epistemic／piggyback 區塊）。

### 3.7 守衛測試（`tests/agent/test_agent_registry_land0_batch1.py`）

1. **註冊面一致性**：24 隻的 `agent_id` 在**五個面**（`personas/` 檔存在、`consciousness` 子類、`AGENT_CLASS_MAP`、`configs/default.yaml` 的 `agents`、`BASE_SCORES`、proxy 三張表）**全部齊備**。
2. **總數**：註冊數 ＝ 34（10 ＋ 24）。
3. **位元等價**：`personas/agent_<id>.md` 與 `docs/persona-drafts/agent_<id>.md` **逐位元相同**（全部 24 隻）。
4. **陰影門閘落地後仍成立**：`personas/` 內 **每個** `agent_*.md` 的 `^## 陰影條款` 計數 ＝ 1（既有 10 隻若無此節，**該斷言只施加於本批 24 隻**，既有 10 隻不動）。
5. **batch2 未落地**：`personas/` 內**不存在**那 12 個檔名。
6. **`NO_DIARY_AGENTS` 未被提前擴充**（仍為 `{"agent_ram"}`）。
7. **既有 10 隻零回歸**：既有 10 隻的 persona／註冊條目**位元級未動**。

### 3.8 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 讓 `personas/` 的落地檔與素材有任一處不同（改一字） | 守衛 3（位元等價） |
| 2 | 讓一隻落地**無陰影條款**的角色（放寬門閘） | 守衛 4 |
| 3 | 讓 batch2 的 12 隻之一落地 | 守衛 5 |
| 4 | 讓五個註冊面少一隻（例如漏 `BASE_SCORES`） | 守衛 1／2 |
| 5 | 提前擴充 `NO_DIARY_AGENTS` | 守衛 6 |
| 6 | 改動既有 10 隻的註冊條目 | 守衛 7 |
| 7 | 覆蓋戰役 A 在 `proxy.py` 的接線 | 守衛 1（proxy 三張表缺項）或既有 proxy 測試轉紅 |

## 4. 驗收（完成的定義）

1. `pytest -q tests/agent/test_agent_registry_land0_batch1.py` **全綠**。
2. **7 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
3. **36 鍵聚焦測試**（依 LAND-0 規格所指的既有聚焦測試；**自行定位**並全綠）**＋ 全庫 `pytest -q tests` 0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
4. **24 隻落地檔與素材位元級相同**（逐檔 diff 為空，給出比對摘要）。
5. **註冊數 = 34**，五面齊備（給出實測表）。
6. **生產零行為變化**：本票**不部署、不重啟**；`data/**` 未被寫入（sha256 前後相同）。
7. 實作紀錄寫 `logs/LAND-0-BATCH1-IMPLEMENTATION.md`（或全文列於回報）。

## 5. 不做（Out of Scope）

- **不得**落地 batch2 的 12 隻（陰影門閘未過）。
- **不得**動 `NO_DIARY_AGENTS`（§0.4）。
- **不得**改 `docs/persona-drafts/**`（唯讀素材）。
- **不得**動既有 10 隻的任何 persona／註冊條目。
- **不得**改 `src/memory/sage/provider.py`。
- **不得**覆蓋戰役 A 在 `proxy.py` 的接線。
- **不得**回溯對話考古以還原「完整版」（Owner 裁決）。
- **不得**做排版美化（`•`→`-` 等）—— 另立 LINT-CHORE 票。
- 不得寫入 `data/**`、不得重啟服務、不得動任何行程、不得觸發 synthetic diary／dream、不得跑 `scripts/manual_*.py`。
- **不得 commit／push**（主大腦驗收後執行）。
- 不得改 frozen contracts。

## 6. 回報格式

1. 改動檔案清單（新增／修改，逐檔一行；24 份 persona 可用彙總＋清單）。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：7 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
4. **24 隻的落地對帳表**（agent_id／中文名／類別名／BASE_SCORES 值／五面齊備 ✔✔✔✔✔／位元等價 ✔）。
5. 註冊總數（10 → 34）與 36 鍵聚焦測試結果 ＋ 全庫回歸。
6. 生產 sha256 前後對帳（零行為變化）。
7. 是否有踩到 Frozen Contract／意外行為；**本票未解決的殘餘問題**（例：batch2 待 SHADOW-0）。

## 7. 與其他工單的關係

- **先決**：SIM-EPIS-PIGGYBACK-1（戰役 A）必須**先合入**（`src/llm/proxy.py` 同一檔）。
- **後續**：SHADOW-0（Notion `3ef5ab1a-15bb-81bf-bc94-c33cb0833bbb`）⇒ 補齊 batch2 的 12 隻陰影層 ⇒ 第二批落地時**同時**把 `NO_DIARY_AGENTS` 擴為 5 筆。
