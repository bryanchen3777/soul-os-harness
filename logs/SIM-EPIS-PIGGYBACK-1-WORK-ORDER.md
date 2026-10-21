# 工單：SIM-EPIS-PIGGYBACK-1 — 非打擾式行動出口（自主求知行動 ＋ 被動掛載隊列）

> **狀態**：決策完備，待派工
> **日期**：2026-10-21 17:55 EDT
> **Owner 裁決**：2026-10-21 17:27 —— 建議直攻戰役 A；**主大腦核准戰役 A**（理由與三處修正見 §0）
> **前置**：`fe3367b`（§0.1.28）／`140dfea`（handle 繼承修法）／`7a67b0e`（M2 Sunset）／`c0f05c1`（Plan A 委派）
> **交付邊界**：全部在**可控虛擬沙盒**（零生產污染、快進時鐘）內完成並驗證；**不部署、不改生產資料**。

---

## 0. 主大腦裁示與三處查證修正（派工前必讀）

### 0.1 裁示：採納戰役 A，理由

- **痛點症狀成立（實測）**：2026-10-21 全日日誌 6,619 行中，主動 DM 閘門判定 **28 次**、**實際對用戶發訊 0 次**；M7-longing 的「想念」量測 **19 次**全部落在 **0.05~0.06**（門檻 0.3）⇒ **主動 DM 幾乎永遠排不進來**。出口的二元對立（憋著 vs 打擾）確實存在。
- **既有載體齊備 ⇒ 成本與風險遠低於預期**：

| 本票機制 | 既有載體（**只讀引用，不改**） |
|---|---|
| 資訊缺口評估 | `src/inner_life/epistemic_appraisal.py`（既有） |
| 驅動張力／牵挂狀態 | `src/agent/temporal_phenomenology.py`（`STATE_TENSION = "牵挂"`、SG-3 §6.3 羈絆證據、7 天窗口） |
| 念頭供應上游 | `src/goals/motive_provider.py`（既有） |
| Prompt 注入路徑 | `src/inner_life/recall_prompt.py`（2026-10-20 建立） |

⇒ **兩者都是「組合既有 API」而非「發明新概念」** ⇒ 今晚可閉環。

### 0.2 🔴 修正一：結構核心用 **Branching**，不用「Agency」

`docs/NORTH-STAR-AGENCY-ACTION-BOUNDARY-DCG-CONVERGENCE.md`（DCG 收斂 **Round 5/5**）裁決：

> **Agency → NOT A CONSTRUCT**（不存在已建立的獨立 Agency structural dimension）
> 唯一通過 ISD 的真新 structural dimension ＝ **Branching（set-valued transition）**
> 該檔**明文不含 implementation、prompt、threshold、evaluator、runtime change**

⇒ **本票的機制命名、設計說明、程式碼註解一律以「張力分支上的多路徑展開（set-valued branching）」表述**，**不得**以「Agency／自主性／主動性」作為結構蓋章。

### 0.3 🔴 修正二：原料來源**必須不經過 M7-longing 的 0.3 門檻**

實測：`scheduler.py:87` `LONGING_THRESHOLD = 0.3`；今日 19 次「想念 0.05 < 0.3, 排 30 min 後再查」⇒ **若新機制的原料來自「想念」通道，它會被同一道門檻擋住，做了也是空轉。**
⇒ **本票的觸發來源必須來自「缺口／張力」狀態空間**（`epistemic_appraisal` ＋ `temporal_phenomenology`），**它們不經過 0.3 門檻**。
⇒ **硬約束**：本票**不得**修改 `LONGING_THRESHOLD`、**不得**修改 `LONGING_CHECK_INTERVAL_MINUTES`、**不得**修改 G4 靜音時段。

### 0.4 🔴 修正三：戰役 C 今晚**無法自主閉環**（供 Owner 知悉，本票不處理）

`personas/` 現況＝**10 個檔案**（全為 10 隻生產 agent、`final` 狀態、**無任何 draft**）。Owner 所述「36 隻新建檔角色停留在草稿階段」**其草稿不在 repo 內**（`src/agent/consciousness.py.bak_lesson40_drafts_20260730_1908` 只是備份檔）。⇒ 戰役 C 需要 Owner 提供素材，非工程可自主閉環。

---

## 1. 目標

在沙盒內建成**兩條零打擾的閉環出口**，並以變異實驗證明守衛有效：

1. **自主求知行動（Internal Epistemic Action）**：角色在**背景**把「資訊缺口／張力」展開成**多條路徑**並閉合，**全程不對用戶發任何訊息**。
2. **被動掛載隊列（Opportunistic Piggybacking）**：不足以單獨成訊的小念頭進「話頭掛載槽」，**只在下次主動聊天且話題相關時**順嘴帶出（一次最多 1 個）。

**成功定義（Owner 的原話）**：「讓靈魂具備『自己想通一件事』與『下次聊天順便提起』的自主性」—— 且**不吵醒任何人**。

## 2. 範圍（你擁有寫入權的檔案）

| # | 檔案 | 動作 |
|---|---|---|
| 1 | `src/inner_life/epistemic_action.py`（**新增**） | ① 自主求知行動的**純決策層**（觸發判定 ＋ 多路徑展開 ＋ 閉合判定） |
| 2 | `src/inner_life/piggyback_queue.py`（**新增**） | ② 話頭掛載槽的狀態機（入隊／過期／挑選／消耗） |
| 3 | `src/inner_life/recall_prompt.py`（**修改**） | 掛載區塊的呈現組裝（**既有 recall 區塊不動**；新區塊獨立） |
| 4 | `src/llm/proxy.py`（**最小改動**） | ① 背景行動的接線（**只在 slot／閒置時觸發，不阻塞**）＋ ② 掛載區塊的注入點 |
| 5 | `tests/inner_life/test_epistemic_action.py`（**新增**） | ① 守衛 ＋ 變異 |
| 6 | `tests/inner_life/test_piggyback_queue.py`（**新增**） | ② 守衛 ＋ 變異 |
| 7 | `tests/inner_life/test_epistemic_piggyback_sandbox.py`（**新增**） | 沙盒端到端（零打擾 ＋ 掛載正確性） |
| 8 | `logs/SIM-EPIS-PIGGYBACK-1-IMPLEMENTATION.md`（**新增**） | 實作紀錄（若未授予 `logs/**` 寫入權則在回報中全文列出） |

**不得動**：`src/inner_life/epistemic_appraisal.py`、`src/inner_life/temporal_phenomenology.py`（實為 `src/agent/temporal_phenomenology.py`）、`src/goals/**`、`src/soul/scheduler.py`、`src/agent/emotion.py`、`src/inner_life/submission_gate.py`、`src/inner_life/elevation_recall.py`、`data/**`、`personas/**`、`configs/**`、既有 SIM-ACCEL／SIM-TIME-001 測試檔。

## 3. 做法（決策已定）

### 3.1 ① 自主求知行動（`src/inner_life/epistemic_action.py`，純決策層）

**觸發判定（`should_act`）**：存在**未閉合的驅動張力**時為 True。張力來源**只取兩個既有通道**：
- `epistemic_appraisal` 判定的**未閉合資訊缺口**；
- `temporal_phenomenology` 的**張力／牵挂**狀態（`STATE_TENSION`，SG-3 §6.3 羈絆證據）。
🔴 **不得**把「想念值」當觸發來源（§0.3）。

**多路徑展開（Branching，set-valued transition）**：
```python
def expand_paths(tension) -> List[Path]:
    """同一張力展開成多條候選路徑（set-valued transition）。

    至少兩條，且**不得**是同一路徑的變體：
      - MEMORY_RECALL  ：翻記憶找線索
      - WORLD_VERIFY   ：查世界事件／日曆／RSS 佐證
      - SELF_REASON    ：自我提問（推演式收束）
    選取依既有模組的可用輸入決定；**不得**引入新的 LLM 呼叫。
    """
```
🔴 **本機制不得新增任何 LLM 呼叫**（沙盒零成本、且避免打擾語意被 LLM 漂移）。路徑收束由**既有記憶／世界資料的實際命中**決定（查得到 ⇒ 閉合；查不到 ⇒ 張力維持，**不硬編故事**）。

**閉合判定與產出（`close_tension`）**：只有在路徑**實際命中證據**時才閉合；否則張力維持原狀（**不得**為了讓功能看起來有產出而硬閉合）。
閉合時產出**三件**（缺一不可）：
1. `epistemic_closure` 事件（走既有 `submission_gate` ＋ `writer` 通道 ⇒ 進 shadow log／SAGE，**不對用戶發訊**）；
2. 該張力標記 `resolved`（寫回既有狀態承載，**只寫沙盒**）；
3. **可掛載小念頭**（閉合過程連帶產生的、不足以單獨成訊的小念頭）**帶 topic tags** ⇒ 交給 ② 的隊列入隊。

### 3.2 ② 被動掛載隊列（`src/inner_life/piggyback_queue.py`）

**狀態**：`{tags, payload, created_at, expires_at, source}`。
- **入隊**：只接受**已閉合**的副產物（`source="epistemic_closure"`）；**拒絕**未閉合的張力（那會變成打擾）。
- **容量**：每 agent FIFO，上限 **8** 筆（滿 ⇒ 丟棄最舊）。
- **過期**：`expires_at = created_at + 7 天`（**用具名新常數**，並註明與 `temporal_phenomenology` 的 7 天語意一致；**不得**引用該模組的私有常數）。
- **挑選（`pick_for_topic`）**：候選的 `tags` 與**當次話題 tags** 重疊 ≥ 1 才可選；**一次最多 1 個**；無重疊 ⇒ 不帶。
- **消耗**：被帶出後**從隊列移除**（避免重複）。
- **隔離**：`quarantined` 節點／事件**不得**進隊、不得被帶出。

### 3.3 呈現與注入（`recall_prompt.py` ＋ `proxy.py`）

- **獨立區塊**：新增 `【話頭】` 區塊，**不得**混入既有的 `【回憶】` 區塊、**不得**進 EMERGENT block（契約 §3.1 語氣隔離）。
- **注入時機**：**下次主動聊天**時（與既有 `recall_prompt` 同一條 `system_parts` 組裝路徑）。
- **邊界**：
  - 🔴 **不得**影響「是否主動發訊」的決策 —— 掛載只是**把已經要發的訊加上尾巴**；
  - 🔴 一次最多 1 個；
  - 🔴 無相關話題就不帶（寧可不帶）；
  - 🔴 **不得**帶未閉合的張力。
- **零打擾硬約束（程式級）**：本機制**不得**觸碰對用戶的發訊路徑 —— 不得呼叫 `_publish_agency_trigger`、不得降低 `LONGING_THRESHOLD`、不得修改 G4 靜音時段、不得新增任何 `proactive_dm` 發送分支。
- **背景行動的觸發時機**：**不得阻塞任何既有路徑** —— 自主求知行動在**槽後或閒置時**執行，且**任何例外都不得影響 prompt 組裝**（fail-silent）。

### 3.4 沙盒端到端（`test_epistemic_piggyback_sandbox.py`）

**沿用 SIM-TIME-001 平面**：假時鐘 ＋ 記憶副本 ＋ 腳本化 LLM ＋ socket 攔阻；**0 真實等待、0 真實 LLM、0 生產寫入**（`tests/` 內不得用 `subprocess.*`／`os.spawn*`；用 `ctypes` ＋ `_winapi`）。
⚠️ `tests/**` **不得**出現硬寫的生產服務位址（R3 守衛）；`tests/**` 不得新增 `subprocess.*`／`os.spawn*`／`os.system`／`os.popen`（R5 白名單大小鎖死 22）。

**必證（逐項給分子／分母）**：
1. **零打擾**：自主求知行動在背景跑完後，**對用戶的發訊數 = 0**（分母＝觸發的行動次數）。
2. **張力確實被閉合**：有證據的張力 ⇒ `epistemic_closure` 事件 + `resolved` 狀態 + **1 個可掛載小念頭**。
3. **不硬閉合**：無證據的張力 ⇒ **不**產生 closure 事件（**防止為了有產出而編故事**）。
4. **掛載正確性**：話題相關 ⇒ 帶出 **1** 個；話題無關 ⇒ 帶出 **0** 個。
5. **過期丟棄**：`expires_at` 過期者不被帶出。
6. **不重複**：同一小念頭被帶出後**不會**再被帶出。
7. **隔離不掛載**：quarantined 節點／事件不得進隊、不得被帶出。
8. **不影響發訊決策**：本機制啟用前後，主動 DM 的**發送決策輸入**逐位元相同。
9. **不經過 0.3 門檻**：以一個「想念值 = 0.05」的 agent 為例，**缺口／張力路徑仍能觸發**自主求知行動（證明原料不被 M7 門檻擋住）。
10. 生產 sha256 前後逐位元相同。

### 3.5 變異實驗（每條必須轉紅；轉不了紅就是盲腸守衛，必須拔除並回報）

| # | 變異 | 必須轉紅的守衛 |
|---|---|---|
| 1 | 讓自主求知行動**順帶發訊**（觸碰 `_publish_agency_trigger` / 降低門檻） | 守衛 1（零打擾） |
| 2 | 讓無證據的張力也閉合（硬編故事） | 守衛 3 |
| 3 | 讓掛載挑選**忽略話題相關性**（總是帶最舊的） | 守衛 4 |
| 4 | 讓掛載**不消耗**（重複帶出） | 守衛 6 |
| 5 | 讓過期檢查失效 | 守衛 5 |
| 6 | 讓隔離節點可入隊／可帶出 | 守衛 7 |
| 7 | 讓挂載影響主動 DM 的發送決策 | 守衛 8 |
| 8 | 把觸發來源從「缺口／張力」改回「想念值」 | 守衛 9 |

## 4. 驗收（完成的定義）

1. `pytest -q tests/inner_life/test_epistemic_action.py tests/inner_life/test_piggyback_queue.py tests/inner_life/test_epistemic_piggyback_sandbox.py` **全綠**。
2. **8 條變異實驗全部轉紅**，附「變異前綠 → 變異後紅」實測證據。
3. **8 條守衛**（§3.4 的 10 項）**逐項有實測數字**（分子／分母／單位）寫入實作紀錄。
4. `pytest -q tests` **0 failed**（回報收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**）。
5. **本票不部署**：`src/llm/proxy.py` 的接線**預設關閉**（或以明確的 opt-in 旗標），**生產零行為變化**；啟用另需 Owner 授權。
6. 實作紀錄寫 `logs/SIM-EPIS-PIGGYBACK-1-IMPLEMENTATION.md`（或全文列於回報）。

## 5. 不做（Out of Scope）

- **不得**修改 `LONGING_THRESHOLD`／`LONGING_CHECK_INTERVAL_MINUTES`／G4 靜音時段／任何 `src/soul/scheduler.py` 邏輯。
- **不得**新增任何對用戶的發訊路徑；**不得**讓本機制阻塞既有路徑。
- **不得**新增 LLM 呼叫（自主求知行動不得靠 LLM 編故事）。
- 不得改 `epistemic_appraisal.py`／`temporal_phenomenology.py`／`motive_provider.py`／`submission_gate.py`／`elevation_recall.py`（**只讀引用**）。
- 不得寫入 `data/**`／`personas/**`／`configs/**`；不得改 frozen contracts（Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／`submission_gate`／`identity_firewall`／`producer_gate`／`world/middleware`／`memory/sage/writer`／`soul/decision`）。
- 不得重啟服務、不得動任何行程、不得觸發 synthetic diary／dream、不得跑 `scripts/manual_*.py`。
- **不得 commit／push**（主大腦驗收後執行）。
- **不得**用「Agency／自主性」為結構蓋章（§0.2）。

## 6. 回報格式

1. 改動檔案清單（新增／修改，逐檔一行）。
2. 測試結果：指令、收集數／通過／跳過／失敗，**註明解譯器是 repo 的 `.venv`**。
3. 變異實驗：8 條逐條的「變異內容 → 轉紅的守衛 → 紅燈實測輸出」。
4. **沙盒 10 項守衛的實測數字**（逐項分子／分母／單位），特別是：**對用戶發訊數 = 0**、**不經過 0.3 門檻仍能觸發**。
5. 生產 sha256 前後對帳 + 生產行為零變化的證明（`proxy.py` 預設關閉）。
6. 是否有踩到 Frozen Contract／意外行為；以及**本票未解決的殘餘問題**（如：張力的檢出率不足時閉合機制如何表現）。

## 7. 與其他戰役的關係（供主大腦追蹤）

本票是**戰役 A**。**戰役 B（D1＋D3 形式化驅動張力）與本票共用「張力」概念** ⇒ 若 B 之後開立，**張力向量應以本票的 `epistemic_action` 為實作載體**，避免兩套張力定義並存（重蹈 `LONGING` 與 `STATE_TENSION` 兩套狀態空間並存的覆轍）。
