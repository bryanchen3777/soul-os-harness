# FEAT-SCHEMA-CONTRADICTION — 實作紀錄（沙盒原型 ＋ 量測）

> **狀態**：RFC 階段交付（**零生產變更、零 schema 變更、零引擎接線**）｜待 Owner 審定
> **工單**：`logs/FEAT-SCHEMA-CONTRADICTION-RFC-WORK-ORDER.md`
> **RFC**：`docs/FEAT-SCHEMA-CONTRADICTION-RFC.md`
> **量測時點**：2026-10-23T02:40:18.794673+00:00（＝2026-10-22 22:40:18 EDT）
> **解譯器**：`C:\Users\bbfcc\.local\bin\soul-os-harness\.venv\Scripts\python.exe`（repo 內 `.venv`，Python 3.11.15）

---

## 1. 改動檔案清單（對照工單 §2.4：恰好 4 個）

| # | 檔案 | 動作 | bytes |
|---|---|---|---|
| 1 | `docs/FEAT-SCHEMA-CONTRADICTION-RFC.md` | 新增（RFC 本體，六節） | — |
| 2 | `src/inner_life/contradiction_probe.py` | 新增（沙盒原型，純函式） | 11,940 |
| 3 | `tests/inner_life/test_contradiction_probe.py` | 新增（守衛 T1～T6） | — |
| 4 | `logs/FEAT-SCHEMA-CONTRADICTION-IMPLEMENTATION.md` | 新增（本檔） | — |

`git status --short`（實作完成時）＝ 4 個 `??`（未追蹤新檔）；**無任何已追蹤檔被修改**
（`git diff --name-only` 為空）。**未** `git add`／`commit`／`push`（依工單 §6）。

---

## 2. 測試結果

```
$ .venv\Scripts\python.exe -m pytest -q tests/inner_life/test_contradiction_probe.py
..............................                                           [100%]
30 passed in 3.77s
```

| 項 | 值 |
|---|---|
| 解譯器 | repo 的 `.venv\Scripts\python.exe`（**非** PATH 全域 pytest）｜Python 3.11.15 |
| collected | 30 |
| passed | **30** |
| failed / error | **0** |
| 耗時 | 3.77 s |
| 覆蓋 | T1（9 項）＋ T2（6 項）＋ T3（3 項）＋ T4（4 項）＋ T5（4 項）＋ T6（4 項）＝ 30 |
| 沙盒 | 全**合成資料**；**不依賴生產資料**、不讀 `data/**` |
| R5 護欄 | 本檔**無** `subprocess.*`／`os.spawn*`／`os.system`／`os.popen` |

> **全庫回歸由主大腦統一跑**（工單分工；已有另一輪回歸在跑，避免雙跑 CPU 競爭）。

---

## 3. 變異 M1～M4（每條**真的轉紅** ＋ 位元還原）

**Harness**：`%TEMP%\featchema_mutation_harness.py`（**在 `tests/` 之外**；R5 護欄禁
`subprocess.*`）。**patch 以 bytes 寫入**（避免 PowerShell 5.1 `Out-File -Encoding utf8` 加 BOM）。

| 變異 | 內容 | 預期轉紅 | 實得轉紅 |
|---|---|---|---|
| M1 | 放寬 `content` 相等判準（相同內容也記矛盾） | T1 | `test_t1_byte_identical_content_is_not_a_contradiction` |
| M2 | 移除 `valid_from_ts > created_ts` 條件 | T2 | T2 全數（6 項）＋ T1 2 項＋ T3 1 項＋ T6 1 項（連帶） |
| M3 | `ts` 取 `created_ts` 而非 `valid_from_ts` | T3 | `test_t3_candidate_fields_are_verbatim_from_the_source_edge`＋`test_t3_ts_is_valid_from_ts_not_created_ts` |
| M4 | 回傳前呼叫 `engine.record_contradiction()` | T6 | `test_t6_module_never_references_record_contradiction`＋`test_t6_probe_does_not_import_the_engine` |

### 3.1 紅燈訊息原文

**M1**（`1 failed, 29 passed`）：
```
E       AssertionError: assert [CandidateCon...type='value')] == []
FAILED tests/inner_life/test_contradiction_probe.py::TestT1CriteriaCorrectness::test_t1_byte_identical_content_is_not_a_contradiction
```

**M2**（`10 failed, 20 passed`；節錄）：
```
E       AssertionError: 應產出 1 組候選矛盾，實得 2
E       assert 2 == 1
E       AssertionError: 同鏈應視為同根，實得 2
_____ TestT2TimeThreshold.test_t2_edge_at_creation_moment_is_not_evidence _____
E       AssertionError: assert [CandidateCon...type='value')] == []
```

**M3**（`2 failed, 28 passed`）：
```
E       AssertionError: assert '2026-10-01T00:00:00+00:00' == '2026-10-05T12:00:00+00:00'
FAILED tests/inner_life/test_contradiction_probe.py::TestT3FieldShape::test_t3_candidate_fields_are_verbatim_from_the_source_edge
FAILED tests/inner_life/test_contradiction_probe.py::TestT3FieldShape::test_t3_ts_is_valid_from_ts_not_created_ts
```

**M4**（`2 failed, 28 passed`）：
```
E       AssertionError: 原型不得呼叫／引用 record_contradiction：['soul_elevation.engine', 'record_contradiction']
E       assert not ['soul_elevation.engine', 'record_contradiction']
E       AssertionError: 原型不得依賴引擎套件：['soul_elevation.engine', 'soul_elevation.models']
FAILED tests/inner_life/test_contradiction_probe.py::TestT6DoesNotWriteEngineState::test_t6_module_never_references_record_contradiction
FAILED tests/inner_life/test_contradiction_probe.py::TestT6DoesNotWriteEngineState::test_t6_probe_does_not_import_the_engine
```

### 3.2 位元還原 sha256 證明

目標檔 `src/inner_life/contradiction_probe.py`：

| 狀態 | sha256 |
|---|---|
| 原始（變異前） | `50c5a88aaf77c40aa10f7374891a6a0709095c3c4f99b492b89fae18cea3ac3c`（11,940 bytes） |
| M1 套用後 | `1a8483ebec03fc1acece87ea5b92da8a6b711f9701b6da4f8947d35b11ca6a70` |
| M2 套用後 | `293466ae930d89da918561468dd76737514d017fa32148ac0dcf734019a5c3d9` |
| M3 套用後 | `4dba5567c3a900fafac69682cf2be6f02cd0de99a8a820b623c36d10edad0a17` |
| M4 套用後 | `206aadaffe7990e755ec025261d9f78c6f15d344b7c0c504e8375deb9294b210` |
| **每條變異還原後** | **`50c5a88aaf77c40aa10f7374891a6a0709095c3c4f99b492b89fae18cea3ac3c`（= 原始，位元相同）** |
| 交付時複核 | `50c5a88aaf77c40aa10f7374891a6a0709095c3c4f99b492b89fae18cea3ac3c`（11,940 bytes）✔ |

`ALL_MUTATIONS_RED_AND_RESTORED=True`（harness 最終判定）。

### 3.3 M4 的一個守衛設計註記（**誠實揭露守衛邊界**）

M4 讓原型**真的**呼叫真實引擎 API（`InternalizingEngine.record_contradiction()`，真的把
`ContradictionRecord` 寫進引擎節點的 `contradiction_pressure`）。但
`ElevationNode` 是 **frozen dataclass**，引擎改的是**自己 registry 內的副本** ⇒
「傳入節點物件逐位元不變」的**行為斷言不會轉紅**；真正轉紅的是 **T6 的結構性守衛**
（AST：模組不得引用 `record_contradiction`／不得 import 引擎）。

⇒ **此類違規（寫引擎狀態）只能靠結構性守衛抓到**；行為性斷言對它是盲腸。本票的 T6
因此**同時**具備行為性與結構性兩道；M4 已證明結構性那道有效。

---

## 4. Q1～Q6 完整答案（生產圖譜**唯讀**實測）

**輸入（量測前後 sha256 相同）**：
| 檔案 | rows | bytes | sha256 |
|---|---|---|---|
| `elevation_nodes.jsonl` | 793 | 601,607 | `486375993c00f7bd3fc5dacaab93853ec09db9694616f9bc518201f91dd83fcb` |
| `elevation_edges.jsonl` | 1791 | 696,344 | `ccb6dc492dbb1f142f8a863a368a37d17237b29d20016248ea4eee3db6abf855` |
| `elevation_lifecycle_events.jsonl` | 252 | 92,544 | `5a196fd9df0d2ddb6a189caaf64b757af14ee1339355fea629abb07442f665a5`（僅狀態對照，未參與判定） |

**量測宇宙**：節點 793 筆（pattern 657／value 125／belief 6／trait 5／essence 0；`active` 793/793）；
邊 1791 筆（`valid_until_ts is None` 1791/1791；`trigger_type == "support_propagation"` 48/1791）。

### Q1 — 依 §2.1 判準，生產圖譜上會產生多少組候選矛盾？

| 分子／分母／單位 | 值 |
|---|---|
| 分子（候選矛盾組數） | **0** |
| 分母（符合前提的 `(N, e)` 配對總數） | **343** |
| 比值 | **0／343 ＝ 0.0000**（組／配對） |

**篩選鏈**：`E(N)` 343／343 → 同 agent＋同 `source_id` 有對應靈魂節點 **48／343** → 再加
**「同一 lineage 根」＝ 0／48** ⇒ **唯一把配對清零的判準是 §2.1 步驟 2 的「同一 lineage 根」**。

### Q2 — 分佈

- **規格候選（0 組）**：agent＝**無定義**；node_type＝**無定義**；`ts` 最早／最晚／跨度＝**無定義**（0 筆）。
- **【診斷】近門配對（48 組，僅差「同一 lineage 根」，非本票判準）**：
  - agent：ruka 6、anna 6、aoi 6、miku 6、ram 6、rem 6、akane 4、mahiru 4、mai 4（組／48）
  - node_type（`N` 側）：`value` 48／48（`belief` 0、`trait` 0）
  - `ts` 最早 `2026-10-21T12:00:15.450271+00:00`／最晚 `2026-10-23T02:01:37.427165+00:00`
  - `ts` 跨度 1 天 14:01:21.976894 ＝ **1.584 天**；不同模擬日 **3**（10-21／10-22／10-23）
  - `e.trigger_type`：`support_propagation` 48／48

### Q3 — Q1 ＝ 0 的成因（哪一條判準濾掉全部配對）

**成因＝§2.1 步驟 2 的「同一 lineage 根」**（48／48 在此被濾掉）。步驟 1（時間門檻）與
步驟 3（`content` 逐位元不等）**都不是**成因（近門 48 組的 content **全部相異**）。

**結構性證據**：靈魂節點 136 筆；`lineage_depth` 分佈 `{1: 136}`（**無 depth ≥ 2**）；
父節點型別 `{pattern: 136}`（**父皆為 pattern**）；父為靈魂節點者 **0** 筆；
相異 lineage 根 **136** 個／136 ⇒ 每顆靈魂節點各有自己的 pattern 根 ⇒ 兩顆**互不相同**的
靈魂節點在現行資料形狀下**永不共根**（除非其中一顆是另一顆的 revise 子節點；現行 **0** 筆）
⇒ 該判準恆不成立 ⇒ 候選恆 0。

⚠️ **未為讓數字非 0 而放寬任何判準、調低任何門檻。**

### Q4 — 以引擎既有計數器，這些候選若寫入累積器，會達門檻嗎？

門檻（**引用**引擎既有常數，零新增）：`LIFECYCLE_N_SUPERSEDE=3`、
`LIFECYCLE_ESSENCE_N_SUPERSEDE=5`、`LIFECYCLE_SUPERSEDE_MIN_DAYS_SPREAD=2`。

- **規格候選（0 組）**：所有節點獨立矛盾數＝**0**、跨模擬日數＝**0** ⇒ **0／136 節點達門檻**
  ⇒ **死鎖不會因規格判準而解除**。
- **【診斷】僅放寬「同一 lineage 根」一條**（非本票設計）：受影響 **15** 顆節點；
  達獨立數門檻 **9／15**；達跨日門檻 **15／15**。逐節點（節錄 4 筆達標者）：

  | 節點（前 12） | agent | 記錄數 | 獨立矛盾數／門檻 | 跨模擬日數／門檻 | 達門檻 |
  |---|---|---|---|---|---|
  | `7906aef95d55` | akane | 4 | 4／3 | 3／2 | 是／是 |
  | `33194d9da2fb` | mahiru | 4 | 4／3 | 3／2 | 是／是 |
  | `536049d72ce4` | ram | 4 | 4／3 | 3／2 | 是／是 |
  | `b031a9fb32f6` | anna | 4 | 4／3 | 3／2 | 是／是 |
  | （其餘 11 顆：獨立數 2／3 或 4／3；跨日數 2／2 或 3／2） | — | 2／4 | — | — | 6 顆否／是、5 顆是／是 |

  ⚠️ 此表**僅供 Owner 判讀可行性**，**非本票設計主張**（近門 48 組 100% 來自 REINFORCE ⇒
  語意誤判風險，見 Q5 與 RFC §6.1 R2）。

### Q5 — 與既有 REINFORCE 傳遞邊（`trigger_type == "support_propagation"`）是否重疊？

| 項 | 值 | 單位／分母 |
|---|---|---|
| 生產 `support_propagation` 邊 | **48** | 筆／1791 |
| 規格候選覆蓋之邊 | **0** | 邊（候選集合為空） |
| **規格口徑重疊組數** | **0／0 ＝ 無定義** | 組 |
| 近門配對用到 `support_propagation` 邊 | **48／48 ＝ 1.0000** | 組／48 |

⇒ 規格判準下無從判斷（候選為 0）；但近門 48 組 **100%** 來自 REINFORCE 傳遞邊 ⇒ 若放寬
lineage 判準，探針的第一信號源**全部**是「命題再確認」而非反例。

### Q6 — `agent_akane` 單獨列出（Phase 1 唯一標桿角色）

| 項 | 值 | 單位／分母 |
|---|---|---|
| 靈魂節點數 | 10 | 筆 |
| `E(N)` 配對數（分母） | **43** | 組 |
| **規格候選（分子）** | **0** | 組／43 ＝ **0.0000** |
| 近門配對 | **4** | 組／43 ＝ 0.0930 |
| 對應節點 | `7906aef95d55`（`value`）vs `a6aad545683c`／`a0e77f23ae95`／`1408be73efa34`／`9f74b9763bc9` | — |
| `ts` | 2026-10-21T12:00:25.792506／10-22T02:00:36.251867／10-22T12:00:13.084868／10-23T02:01:37.724317 | UTC ISO |
| `e.trigger_type` | `support_propagation` 4／4 | 組／4 |
| **【診斷】** | 獨立矛盾數 **4／3**、跨模擬日數 **3／2** | 達門檻＝是／是 |

---

## 5. RFC 六節完成確認

| # | RFC 節 | 狀態 |
|---|---|---|
| 1 | 問題陳述（§0.2 死鎖：矛盾要靠 supersede 產生、supersede 要靠矛盾） | ✔ 完成 |
| 2 | 探針來源規格（判準／欄位形態／計數／門檻逐字沿用既有 ＋ 2.5 歧義收斂） | ✔ 完成 |
| 3 | 資料契約影響評估（**零欄位新增** ＋ 793/793 實測證據） | ✔ 完成 |
| 4 | 沙盒量測結果（Q1～Q6，含分子／分母／單位） | ✔ 完成 |
| 5 | Phase 2 落地提案（**本票不實作**）＋ 所需 Owner 授權 ＋ 前置條件 O-1／O-2／O-3 | ✔ 完成 |
| 6 | 風險與不做（風險 R1～R6 ＋ 不做自證 ＋ Frozen Contract 自證） | ✔ 完成 |

**第 3 節「零欄位新增」聲明（逐字）**：

> **零欄位新增、既有生產 JSONL 不追加任何欄位。**

---

## 6. Frozen Contract 自證（逐項）

| Frozen 項 | 自證 |
|---|---|
| Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯 | 本票改動**僅** 4 個新檔；**無** `src/agency/*`／`src/soul/**`／`src/memory/sage/**` 改動 |
| `tests/test_observability_completeness_1.py` 的 `frozen_dirs` 11 筆 | 本票**未動**該檔（`git diff --name-only` 空）；11 筆全保留 |
| `soul_elevation` 的 `ContradictionRecord` 欄位族／`LIFECYCLE_*` 門檻／`_count_independent_contradictions()`／`_contradiction_spread_days()` | 外部 repo **唯讀**引用；零寫入（唯讀引用可由 `soul-elevation` 自身 git 狀態另行覆核） |
| 戰役 B Phase 1 三模組（`tension_scoring.py`／`tension_closure_loop.py`／`drive_profile.py`） | 凍結未動（本票檔案清單不含） |
| 引擎寫入路徑 | 原型**零** `soul_elevation` import（T6 AST 守衛；M4 變異證明會轉紅） |

---

## 7. `data/**` 位元恆等證明

### 7.1 全樹 manifest（實作前後）

| 項 | 值 |
|---|---|
| 量測前 manifest（22:23 EDT 前後） | **7,733** 檔 |
| 量測後 manifest（22:47 EDT 前後） | **7,738** 檔 |
| added / modified / removed / unchanged | **5 / 32 / 0 / 7,701** |
| 🔴 **全樹 0 差異** | **❌ 未達成**——差異**全部**來自**持續執行的生產服務**，**非本票** |

### 7.2 差異歸因（**每一筆**）

| 類別 | 檔案 | 寫入者 |
|---|---|---|
| TTS 音訊（新增 3 檔） | `data/tts/agent_ruka/20261023T023208_609828.mp3`、`data/tts/agent_yua/20261023T023203_240619.mp3`、`data/tts/agent_yua/20261023T023210_825583.mp3` | 生產服務（即時回覆） |
| 對話 | `data/conversations/bryan_test_agent_{ruka,yua}_private.json`、`group_chat.json` | 生產服務 |
| 記憶 | `data/memory/*/memories.jsonl`、`graph.sqlite-wal/-shm`、`continuity_*.jsonl`、`loader_trace.jsonl`、`data/memory.db` | 生產服務 |
| 狀態／心跳 | `data/state/*`（7 檔）、`data/heartbeat_trace.log`、`data/heartbeats/telegram_channel.json`、`data/logs/watchdog.log`、`data/server_nohup.{log,err}`、`data/crash_dumps/.last_collected.txt` | 生產服務 |
| 情感／關係 | `data/agents/*/emotional-state.json`、`data/soul/*/relationships.json`、`data/soul/capability_projection_trace.jsonl` | 生產服務 |
| 世界／投影 | `data/world/perception_trace.jsonl`、`data/world/world_log/2026-10-23.jsonl`、`data/elevation/elevation_projection_trace.jsonl` | 生產服務 |

### 7.3 本票實際讀取的三個檔案：**位元恆等**

| 檔案 | 量測前 sha256 | 量測後 sha256 | 結論 |
|---|---|---|---|
| `data/elevation/elevation_nodes.jsonl` | `486375993c00f7bd3fc5dacaab93853ec09db9694616f9bc518201f91dd83fcb` | 同左 | **UNCHANGED（位元相同）** |
| `data/elevation/elevation_edges.jsonl` | `ccb6dc492dbb1f142f8a863a368a37d17237b29d20016248ea4eee3db6abf855` | 同左 | **UNCHANGED（位元相同）** |
| `data/elevation/elevation_lifecycle_events.jsonl` | `5a196fd9df0d2ddb6a189caaf64b757af14ee1339355fea629abb07442f665a5` | 同左 | **UNCHANGED（位元相同）** |

（量測腳本內建量測前後自比；三檔皆 `True`。）

### 7.4 「閒置蠕動」歸因（客觀，非自我聲明）

見 §9 附錄（於**無任何 agent 活動**的 75 秒窗口內連拍兩份 manifest，`data/` 仍持續變動
⇒ 寫入者是**生產服務**，與本票無關）。

⇒ **結論**：工單 §5.6 的「全樹 0 差異」在**生產服務持續運作**的機器上**結構性不可達成**
（除非停服）。本票能證明的是：**本票只讀不寫**（讀取側三檔位元恆等 ＋ 差異全部落在
即時服務寫入面 ＋ 閒置窗口仍在變動）。

---

## 8. 與工單前提不符的實測（**回報，不自行改設計**）

| # | 工單前提 | 實測 | 處置 |
|---|---|---|---|
| P1 | §0.1「793/793 節點皆含 `contradiction_pressure`；793/793 皆為 `[]`」 | **完全吻合**（獨立複核：793/793 含欄位、793/793 `== []`、0/793 非空、型別唯一為 `list`） | 採納為 RFC §1.2／§3.3 證據 |
| P2 | §0.2「`record_contradiction()` 唯一呼叫點在 `supersede()` 內」 | **完全吻合**：`soul_elevation` 全套件中，`record_contradiction` 定義於 `engine.py:990`、**呼叫點僅 `engine.py:1178`**；harness repo **零**生產呼叫點 | 採納為 RFC §1.1 死鎖證據 |
| P3 | §3 Q5「REINFORCE 傳遞邊（部署首跑 **33** 筆）」 | **實測 48 筆**（`trigger_type == "support_propagation"` 48／1791） | 33 是**部署首跑**的歷史值；夜槽其後續跑再追加 ⇒ 以 **48** 為準（RFC §4.5 已載） |
| P4 | §2.1 步驟 2「同一 lineage 根」預設可成立 | **現行資料形狀下恆不成立**（136 顆靈魂節點 depth 皆 1、父皆 pattern、相異根 136）⇒ **Q1 = 0** | **不自行放寬**；成因寫入 RFC §4.3，並把裁決題升級為 Phase 2 前置條件（RFC §5.2 的 O-1／O-2／O-3） |
| P5 | §2.2 `CandidateContradiction` 欄位清單 | 本票另加 `agent_id`／`node_type`（工單 §2.2 已列）——**一致**，無出入 | — |
| P6 | §4.1 T4「以『傳入不存在的路徑／不給路徑參數』證明其無 IO 依賴」 | 原型 API **本來就沒有**路徑參數（`nodes`／`edges`／`agent_id`）⇒ 採**更強**證明：公開 API 簽章無路徑參數 ＋ AST 無檔案 IO ＋ 兩次呼叫逐位元相等 | T4 已加簽章斷言與 AST IO 斷言 |
| P7 | §5.6「`data/**` 位元恆等 ⇒ 0 差異」 | 在**生產服務持續運作**下**不可達成**（全樹 5 新增／32 修改；**但讀取側三檔位元恆等**） | 已做客觀歸因（§7.2／§7.4），**不假稱 0 差異** |

---

## 9. 交付後待主大腦處理（**本票不做**）

| # | 事項 | 說明 |
|---|---|---|
| 1 | `tests/test_observability_completeness_1.py` 的 `allowed` **新增一筆**：`"src/inner_life/contradiction_probe.py"` | **本票不動該檔**（工單 §2.4）。`frozen_dirs` 11 筆**不得**更動 |
| 2 | `git add` 4 個新檔（否則 `tests/infra/test_repo_hygiene_tracked_tests.py` 會紅） | 該守衛讀 `.git/index`；未追蹤的 collectable 測試檔 ⇒ 紅。**成因＝本票檔案尚未 `git add`**，非缺陷 |
| 3 | 全庫回歸（`.venv\Scripts\python.exe -m pytest -q tests`） | 由主大腦統一跑（避免雙跑 CPU 競爭） |
| 4 | 合入（`git add`／`commit`） | 本票**未**執行任何 git 寫入操作 |

### 附錄 — 閒置窗口蠕動證據（原始輸出）

於**無任何 agent 活動**的 75 秒窗口內連拍兩份全樹 manifest（`%TEMP%\featchema_idle_churn.py`）：

```
files=7738 manifest=...data_manifest_idle_c.txt
manifest C taken; sleeping 75s with no agent activity ...
files=7738 manifest=...data_manifest_idle_d.txt
manifest D taken
IDLE WINDOW: added=0 modified=2 removed=0
  ~ data/heartbeat_trace.log
  ~ data/heartbeats/telegram_channel.json
  data/elevation/elevation_nodes.jsonl: UNCHANGED
  data/elevation/elevation_edges.jsonl: UNCHANGED
  data/elevation/elevation_lifecycle_events.jsonl: UNCHANGED
```

⇒ **本票完全停手 75 秒期間，`data/` 仍被生產服務改動 2 檔**（心跳）⇒ §7.2 的差異歸因
（寫入者＝生產服務）成立；而**本票讀取的三個圖譜檔在該窗口仍位元恆等**。

---

## 10. 附錄 — 本票產出的量測／變異工具（**均在 `%TEMP%`，非 repo**）

| 檔案 | 用途 |
|---|---|
| `%TEMP%\featchema_measure.py` | Q1～Q6 量測（唯讀；內建量測前後 sha256 自比） |
| `%TEMP%\featchema_mutation_harness.py` | M1～M4 變異（bytes patch ＋ 位元還原） |
| `%TEMP%\featchema_data_manifest.py` | `data/**` 全樹 sha256 manifest |
| `%TEMP%\featchema_manifest_diff.py` | manifest 差異歸因 |
| `%TEMP%\featchema_idle_churn.py` | 閒置窗口蠕動歸因 |
| `%TEMP%\featchema_recon.py`／`recon2.py`／`recon3.py` | 勘查期量測（篩選鏈定位） |
| `%TEMP%\featchema_mutations\M{1..4}.txt` | 四條變異的完整 pytest 輸出（紅燈原文） |
