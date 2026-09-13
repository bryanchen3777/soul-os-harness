# SG-3 關係帶尺度校正 + TA-2 換軌 + impression_tags 寫入規範（設計契約）

- **工單**：SG-3（**DOCS-ONLY / CONTRACT-FIRST**：0 code、0 測試改動、0 門檻常數改動、0 服務重啟、0 commit）
- **唯一產出**：`docs/SG-3-RELATIONAL-CONTRACT.md`（本檔）
- **契約日**：2026-09-13（對齊 current HEAD 語境；本工單**起始** HEAD == `ce66bd1`）
- **說明（併發變更，非本工單所為）**：本契約作業期間，另一個工單在 repo 內提交了 `e6eefd6`（`feat(volition): persist four-way decision trace (additive observability)`，含 `src/soul/decision_trace.py` 及各處 P1-1 觀測面）與 `111e14e`（`docs: sync Current HEAD to e6eefd6`）→ 收工時 HEAD == `origin/main` == `111e14e`。**該批變更未觸碰關係帶門檻、TA-2 判定或 `impression_tags`**（本契約 §5/§6/§7 的所有門檻與行號仍以 `ce66bd1` 基線為準，且已複驗 `src/social/relational_bands.py`、`src/soul/temporal_phenomenology.py`、`src/soul/relationships.py`、`src/soul/dream_event.py` 的 mtime 皆早於本工單開始 → 未被任何一方改動）。
- **入口依據**：`docs/INNER-LIFE-AUDIT-1.md`（READ-ONLY 審計，HEAD `9cc0ad9` 時點）+ SG-0/G-1 之 P1-A-RECON 系列（下稱「前序已驗證事實」）
- **性質**：**設計契約，非施工授權。** 本契約不自行生效；實作需另開工單。canonical 狀態以 `logs/ENGINEERING_STATE.md` 為準。

---

## §1 範圍與修訂關係

### 1.1 本契約要解的三件事

| # | 病徵（引用前序已驗證事實） | 本契約的處置 |
|---|---|---|
| A | 關係帶 100/100 凍結在 `stranger` | 重標定三個升帶門檻 + 降帶門檻（§5），並新增可達性不變量（§3 INV-5） |
| B | TA-2 三態 10/10 恆「無感」 | 資格判據從退役的 `confidence` 換軌到 D4 真值源 + 未衰減的客觀計數（§6） |
| C | `impression_tags` 100/100 空 | 定義唯一合法寫入管道 + Privacy Gate 邊界（§7） |

### 1.2 修訂／取代既有契約條文（明文標示）

| 被修訂者 | 條號 | 原內容 | 本契約的處置 | 依據 |
|---|---|---|---|---|
| `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md` | **§3.3 升帶／降帶門檻表** | `stranger→known`：`reply≥1` **或** `co≥2`；`known→familiar`：`reply≥3` **且** `co≥5`；`familiar→close`：(`reply≥10` 且 `co≥15`) **或** (`dream≥4` 且 `reply≥5`)；降帶：連續 **>30 天** 無訊號 | **本契約 §5 修正／取代本表的「數值」**。`stranger→known`、`known→familiar`、`familiar→close`、`DEMOTE_DAYS` 全部依 §4 產率模型重標定 | SG-1 §3.3 自身留有授權條款：「上表數值為契規定稿閾值，**實現期經主大腦複核可調，但形態凍結**」。本契約即行使該條：**形態 0 變更**（整數計數、離散判定、0 加權、0 浮點、0 排序打分），**僅數值重標定** |
| `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md` | **§9.3 本期 additive-only 變更清單** | 關係帶狀態機行為 | **0 變更**：本契約不新增 additive 欄位、不改 schema 4.2、不改寫路徑；只改純函式門檻數值 | — |
| `docs/TEMPORAL-PHENOMENOLOGY.md`（TA-2 原設計） | **§3.2 資格門檻** | 「牽掛浮現的資格判定 = 復用 M5.13-3 親密度 Band（`confidence ≥ 0.5`，熟悉 Band 及以上）」 | **本契約 §6 修正／取代**：資格判據改為「羈絆證據」四級優先序（band / impression_tags / interaction_count / last_interaction_at），**`confidence` 完全退出 TA-2 判定鏈**；`TENSION_ELIGIBILITY_MIN_CONFIDENCE = 0.5` 廢止 | D4（SG-1 §1）已把關係域 `confidence` 降級為**只讀遺留欄位**、真值改為 `relational_band + impression_tags`（`src/soul/relationships.py:105-108`）。TA-2 仍以退役欄位當**唯一前置短路閘門**＝契約層自身矛盾。另：SG-1 §1 D4 原文即註明「離散帶階段躍遷（**對照 TA-2 三態先例**）」——兩者本應同源，本契約使其真正同源 |
| `docs/TEMPORAL-PHENOMENOLOGY.md`（TA-2 原設計） | **§2.2 / §3.1 第 2 點** | 「三態轉換非連續：**無閾值函式**（沒有「沉默 > N 天 → 牽掛」）」——但 §3.1 觸發情境表又寫「間隔在**該關係的歷史正常節奏內** / **明顯超出** / **遠超**」 | **本契約 §6.2 明文採用「離散邊界形式」**（`<24h` / `24h~7d` / `≥7d` 三格，現行實作 `temporal_phenomenology.py:117-121` 已是此形式）。**標示為 TA-2 原設計的自我矛盾**：原設計一邊禁止閾值函式、一邊以三格現象化描述分界。本契約選擇「**離散三格枚舉（discrete partition）**」而非「連續閾值函式（continuous threshold function）」，並保留原設計真正要禁止的東西：**0 張力分數、0 強度公式、三格之間 0 中間值** |
| `docs/TEMPORAL-PHENOMENOLOGY.md`（TA-2 原設計） | **§3.3 reflect-only 加權邊界 / T1 防線** | 第三行內嵌「但這絕不代表必須主動聯絡」 | **0 變更**（明文保留，見 §6.5） | — |
| `docs/SOCIAL-DIFFUSION-CONTRACT.md`（SI-2.1） | **§5.1 隱私可見性判定表** | 與 Bryan 的 1:1 私聊 DM：默認 `private`，**嚴格攔截於廣播總線之外** | **0 變更**；本契約 §7.3 在**消費端**再加一道對稱邊界（私聊內容不得流入跨 agent 可觀測欄位） | — |

### 1.3 0 衝突聲明

除 §1.2 明列的兩項修正（SG-1 §3.3 數值、TA-2 §3.2 資格判據）外，本契約與下列既有契約**0 衝突**：SG-1 §2（schema 4.2）、SG-1 §5（Motive target 值域）、SG-1 §6（No-Scoring）、SG-1 §7（防線核對表）、SI-2.1 三道防線、C-3.1（關係感知渲染塊）、C-2.1、EH-1/EH-4、LS-1、TG-1、MEM-VISIBILITY-C1/C2。

> ⚠️ 一項「契約 vs 實作漂移」的誠實記載（**本契約不修，見 §11 OQ-5**）：`src/soul/decision.py:416-417` 的 `_build_relationship_summary` 仍把 `confidence` 當真值渲染成「信任度：0.00」（生產此時 10/10 皆 0.00）；`src/soul/dream_event.py:158` 的 `_pick_dream_target` 仍以 `confidence`（缺省 0.3）排序挑夢境對象。兩者皆在 SG-1 D4「只讀遺留欄位」的字面邊界內（未寫入），但仍**讀為真值**，屬 D4 落地後的遺留讀側耦合。

---

## §2 現況證據摘要（**引用**，非本契約重新推導）

以下數字全部**引用自前序已驗證事實**；本契約僅在必要處**重新複驗**並標明「（SG-3 複驗）」。

| # | 事實 | 數值 | 來源 |
|---|---|---|---|
| E1 | 關係帶凍結 | `relationships.json` 10 agents × 10 others = **100/100** `relational_band = "stranger"`；`band_updated_at` **0 筆存在**；`impression_tags` 非空 **0/100** | INNER-LIFE-AUDIT-1 §4 P0-A（SG-3 複驗：100/100 stranger、0/0 tags 非空、0 筆 band_updated_at 鍵） |
| E2 | 升帶計數器 | `objective.reply_exchanges` **100/100 = 0**；`dream_exchanges` **100/100 = 0**；`co_presence_sessions`：`0` 者 **90** 條、`1` 者 **10** 條（門檻 2 → **永遠差 1**） | INNER-LIFE-AUDIT-1 §4 P0-A（SG-3 複驗一致） |
| E3 | `reply_exchanges` 無生產者 | `perception_trace.jsonl` **11,099** 行（SG-3 複驗）中 `event_type=="reply"` **0 行**；`extra.event_kind` **全為 None（11,099/11,099）**（工單所述「`event_kind=="social"` 0 行」成立，且更強：該鍵**完全不存在**）；全 `src/` 只有 `SOCIAL_WORLD_EVENT` 的**訂閱端 + schema 定義**（`src/world/middleware.py:63,259,267,287,354`、`src/eventbus/schema.py:61,337`），**0 個生產端 publish**；`src/social/producer_gate.py` 只被 `tests/` 引用 | 前序已驗證事實 1（SG-3 複驗） |
| E4 | `co_presence_sessions` 結構性不可達 | 供給 = 每 **6–12h** 抽 **2 隻 agent**（`src/soul/scheduler.py:160-163` 常數、`:1007` `_fire_shared_event`、`:1058` `_fire_cross_chat` 皆 `random.sample(self._all_agents, 2)`）→ **45 個無序 pair**；`data/soul/interactions.jsonl` 全檔 **19 行**（`cross_chat` 10 / `shared_event` 9），時間跨度 **2026-08-23T01:26:48Z ~ 2026-09-10T20:16:19Z**（18.8 天）；**pair 級 lifetime 最大值 = 2**（SG-3 複驗：16 個 pair 出現過，最大值 2）；門檻為「**同一 trailing 24h 窗** 內 `co ≥ 2` 且同一 pair」→ 實測窗內 `co_max = 1`，**永遠差 1** | 前序已驗證事實 2（SG-3 複驗修正：19 行跨度為 08-23~09-10，非 09-03~09-10） |
| E5 | `dream_exchanges` 設計性恆 0 | `src/social/relation_settlement.py:16-18,153` 明載 v1 無方向性持久載體（diary dream entry 不含 target）→ 生產計數恆 0 | 前序已驗證事實 3（SG-3 複驗） |
| E6 | TA-2 卡在已退役欄位 | `confidence ≥ TENSION_ELIGIBILITY_MIN_CONFIDENCE = 0.5`（`src/soul/temporal_phenomenology.py:50`）為**唯一前置短路閘門**（`:114`，全檔唯一使用點）；增益 `+0.02/次互動`（`relationships.py:63`）與衰減 `−0.02/天`（`:67`、`:272-276`，anchor = per-entry `last_interaction_at`，`:239`）**同量級**；實測 `confidence_max = 0.0979`（agent_rem→user_bryan）、`≥0.5` **0 條**（SG-3 複驗）→ 10/10 恆「無感」，**elapsed 完全不看**（`temporal_phenomenology.py:112-121`） | 前序已驗證事實 4（SG-3 複驗） |
| E7 | 同名不同尺度陷阱 | TA-2 的 `0.5` 對應 **legacy M5.13-3 confidence band** 的「熟悉」（`src/llm/proxy.py:350` `_RELATIONSHIP_BAND_FAMILIAR = 0.5`），而 SG-2 的 `familiar` 是 **counter band**（`reply≥3 且 co≥5`）→ **兩者不可等價替換** | 前序已驗證事實 4（本契約 §6.4 明文禁止） |
| E8 | `impression_tags` 寫入端存在但呼叫端永不傳值 | 參數定義 `relationships.py:338`、寫入邏輯 `:360-369`；唯一生產呼叫 `src/soul/dream_event.py:483-485` **不傳 `impression_tags`** → 永遠走 `is not None` False 分支；且 `_extract_impression`（`dream_event.py:717-764`）**根本不產生 tags**（只回單一字串）→ 實測 100/100 空 | 前序已驗證事實 5（SG-3 複驗） |
| E9 | legacy `impression` 只對 Bryan 全空 | `user_bryan.impression` **10/10 空**；**非 Bryan 對子 40/90 有內容**（SG-3 複驗；審計原文作「40 條非空」）。原因：`on_dream` 的 target 永遠是「confidence 最高的其他 agent」（`dream_event.py:142-163`），**永遠不是 Bryan** | 前序已驗證事實 5（SG-3 複驗） |
| E10 | Bryan 軸唯一未衰減的羈絆證據 | `user_bryan.last_interaction_at` 全 10 位皆有近期值（最新 `agent_rem` = **2026-09-13T01:04:40Z**）；`user_bryan.interaction_count` 實測 **31 / 32 / 48 / 54 / 95 / 97 / 125 / 230 / 237 / 385**（最小值 31） | 前序已驗證事實（SG-3 複驗） |
| E11 | 既有門檻表 | `src/social/relational_bands.py:62-66,69-73,76-79,82,115,163-190`；閾值全整數、0 浮點（AST 硬斷言 `tests/social/test_relational_bands.py:151-160`） | 前序已驗證事實 6（SG-3 複驗） |
| E12 | Frozen Contract 清單 | Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯（`logs/ENGINEERING_STATE.md:110`）；另 SubmissionGate、SI-2.1 三道防線（Privacy Visibility Gate / ProducerGate / Identity Firewall） | 前序已驗證事實 7 |
| E13 | 服務四埠 | `:8000` pid **14056**；`:8765` pid **9576**；`:8766` pid **9592**；`:8767` pid **9584**（SG-3 複驗：`Get-NetTCPConnection -State Listen` 四埠 OwningProcess 完全一致） | 前序已驗證事實 8（SG-3 複驗） |
| E14 | 工作區狀態 | 本工單**起始** HEAD == `ce66bd1`（動工前有 1 筆 **modified** `src/soul/scheduler.py`，**非本工單所為**）。作業期間另一工單提交 `e6eefd6` + `111e14e` → 收工 HEAD == `origin/main` == `111e14e`。**本工單收工時 `git status --porcelain` 的 tracked 變更數 = 0**（僅本契約 1 筆 untracked 新增） | SG-3 實測 |

**NO DATA / 待驗**（拒絕以推測填空）：
- `[TEMPORAL ANCHOR]` 的**實際注入文本**：成功路徑不留日誌（`src/llm/proxy.py:1076` 僅失敗時 `logger.debug`）→ 只能由 `confidence` 確定性推導「10/10 恆為無感」，**不可由日誌觀測**（沿用 INNER-LIFE-AUDIT-1 §4 P0-B）。
- `silence_duration` 量測點：全 `src/` 不存在（僅 `last_interaction_period`，`proxy.py:976-995`）。
- 四元決策分佈：結構性不可觀測（INNER-LIFE-AUDIT-1 §2）。

---

## §3 不變量（INV-1 ~ INV-9）

| # | 不變量 | 可硬斷言形式 |
|---|---|---|
| **INV-1** | **No-Scoring 剛線**：關係帶與三態的一切門檻全為**整數**；0 浮點常量、0 加權公式、0 乘積／對數、0 排序打分 | `relational_bands.py` AST 掃描 `ast.Constant` 之 `float` 集合為空（既有斷言 `tests/social/test_relational_bands.py:151-160` 保留）；`"weight" / "affinity" / "score"` 字面不出現（`:162-166` 保留） |
| **INV-2** | **門檻只回答「夠不夠格」**，不進入任何算式的乘法因子；三態之間**無中間值**，帶之間**無強度** | `evaluate_band` 值域 ⊆ 四帶枚舉；`classify_temporal_state` 值域 ⊆ 三態枚舉 |
| **INV-3** | **寫路徑物理隔離**（SG-1 §2.4）：唯一寫入口 `RelationshipsStore`；0 經 `InnerLifeWriter` / `SubmissionGate.verify` / `GraphStore.add_fact` | 靜態：`src/social/**` 與 `src/soul/relationships.py` 0 import agency/actuator/sage writer |
| **INV-4** | **0 新定時器 / 0 新 tick / 0 新 sleep**：關係評估與三態現算皆掛既有時機（30s wake 並列分支 / prompt 現算） | `src/soul/scheduler.py` 0 新增 timer/sleep |
| **INV-5** | **可達性不變量（PD-2，本契約新增，硬性）**：**每一個 band 轉移，都必須在「已宣告的實測訊號產率 R」下，於明確宣告的時間地平線 T 內可達。** 未在 §5 表格中附可達性推導的門檻 ＝ **契約違規**（不可實作） | 每個門檻列必須同時給出 `(R, T, P(可達))`；新門檻若使 `P(可達) = 0`（結構性不可達，如 E4 的「同窗 `co≥2`」）→ 拒絕合入 |
| **INV-6** | **降帶不得劇烈回退**：降帶門檻必須 ≥ 該軸訊號的平均到達間隔；且每次結算至多降 1 帶、底帶不降 | `DEMOTE_DAYS` 與 §4 之 `1/R_pair` 比較；`demote_band("stranger") == "stranger"` |
| **INV-7** | **TA-2 保守性**：換軌後**不得提高 `transmit` 率**；`reflect-only 加權` 語義不變；三態注入文本必須保留 T1 防線措辭「這絕不代表必須主動聯絡」；0 per-agent if/else | 三態文案表三條皆含防線句（`temporal_phenomenology.py:74-81` 保留）；`classify_temporal_state` 簽名**不接收 `agent_id`** |
| **INV-8** | **fail-silent**：任何讀取失敗 / 缺 entry / 壞時間戳 → 不編造、不 crash、不阻塞 prompt（回 `""` 或保守態） | `format_temporal_anchor` 例外路徑回 `""` |
| **INV-9** | **Privacy Gate 邊界（SI-2.1 防線 2 的消費端對稱）**：**與 Bryan 的 1:1 私聊內容，不得流入任何跨 agent 可觀測欄位**（含 `impression_tags`、`impression`、`relational_band`、`objective.*`、B5 種子素材、`[關係感知]` 塊） | 靜態：`impression_tags` 的寫入呼叫端**必須**來自 §7.2 白名單；任何以 `is_private` / `mode=="private"` / TG 1:1 來源為輸入的 tag 產生路徑 ＝ 違規 |

---

## §4 訊號產率模型 R 與可達性推導（PD-2）

### 4.1 宣告（本契約數字，全部由 §2 實測推導）

| 符號 | 名稱 | 值 | 推導（一步） |
|---|---|---|---|
| `PAIRS` | agent 對數 | **45**（`C(10,2)`） | `_all_agents` = 10 位生產角色（INNER-LIFE-AUDIT-1 §1） |
| `SAMPLES_PER_DAY_theory` | 理論抽樣速率 | **2/9h → 5.33 sessions/day** | 抽樣區間 6–12h，均值 9h（`scheduler.py:160-163` 常數）→ 2 sessions/9h |
| `N_SESSIONS` | 實測 session 數 | **19**（08-23~09-10） | E4 實測 |
| `T_OBS` | 實測跨度 | **18.8 天** | E4 實測 |
| `SAMPLES_PER_DAY_obs` | 實測抽樣速率 | **1.056 sessions/day** | `19 / 18.8` |
| `AVAILABILITY` | 有效供給率 | **≈ 0.20** | `1.056 / 5.33`（服務可用性打折：審計實測 45 天內 watchdog restart **326** 次、`CAP REACHED` 1,742 行橫跨 11 天） |
| **`R_co_system`** | 系統級共在訊號產率 | **1.056 /day** | `SAMPLES_PER_DAY_obs` |
| **`R_co_pair`** | **pair 級**共在訊號產率 | **0.02246 /pair/day** | `1.056 / 45` |
| `E[gap_pair]` | pair 平均到達間隔 | **44.5 天** | `1 / R_co_pair` |
| **`R_reply`** | reply 訊號產率 | **0 /day（現況 0 生產者，E3）**；本契約 §5 給定**條件產率**（見 4.3） | E3 |
| **`R_dream`** | dream 訊號產率 | **0 /day（設計性恆 0，E5）** | E5 |
| **`R_bryan`** | Bryan 軸互動訊號產率 | `user_bryan.interaction_count` 31–385/agent，`last_interaction_at` 全體近期（E10）→ **≥ 0.1 /agent/day（保守下界）**；實測最小間隔範例：`agent_rem` 交互後 0 天、`agent_yua` ≈ 0.54 天 | E10 |

> **R 的宣告範圍**：本契約的門檻在 `R_co_pair = 0.02246 /pair/day`、`R_reply = 0 /day`、`R_dream = 0 /day` 下**必須可達**；Bryan 軸走 `R_bryan`。

### 4.2 可達性推導表（PD-2 / PD-3 的定量依據）

以 `co_presence` 為**唯一可用動力**，pair 級到達近似 Poisson(`λ = R_co_pair × T = 0.02246 × T`)：

| 時間地平線 T | `λ` | `P(≥1 訊號)` | `P(≥2 訊號)` | `P(≥4 訊號)` | `P(≥5 訊號)`（**舊 `known` 門檻**） | `P(≥15 訊號)`（**舊 `close` 門檻**） |
|---|---|---|---|---|---|---|
| 1 天（舊「同一 24h 窗」） | 0.0225 | 2.2% | **0.03%** | ~0 | **~0（結構性不可達，E4 實證）** | ~0 |
| 7 天 | 0.1572 | 14.6% | 1.1% | 0.002% | ~0 | ~0 |
| 30 天 | 0.6738 | **49.0%** | 14.7% | 0.5% | ~0 | ~0 |
| **90 天** | 2.0214 | **86.8%** | **59.97%** | 14.7% | 0.004% | ~0 |
| 180 天 | 4.0428 | 98.2% | 91.2% | **57.5%** | 0.02% | ~0 |
| 365 天 | 8.1979 | 99.97% | 99.75% | **96.3%** | 0.4% | ~0 |
| 730 天 | 16.40 | 100% | 100% | 100% | 99.97% | 0.03% |

**由表得出的四個硬結論**

1. **舊表在 R 下實質不可達**：舊 `known→familiar` 的 `co≥5` 需 **中位 208 天**（365 天可達率僅 0.4%）；舊 `familiar→close` 的 `co≥15` 需 **中位 653 天**（730 天才 0.03%）。**舊表即 INV-5 定義下的契約違規。**
2. **「同一 24h 窗 `co≥2`」是結構性不可達**（非「很難」，是「需要同一 pair 在 24h 內被抽中 2 次」：單日 `P(≥2) = 0.03%`，窗內訊號數期望僅 0.0225）。E4 的「永遠差 1」正是此結構的實證。
3. **可訂約的地平線（本契約宣告的 T）**：`familiar` 取「累計 ≥2 且落在 ≥2 個不同 24h 窗」→ **中位可達時間 ≈ 75 天**（`λ=1.678`）；`close` 取 4 個窗 → **中位 ≈ 164 天**（`λ=3.672`）。
4. **單窗內 ≥1 訊號的中位 ≈ 31 天**（`λ=0.693`）→ `stranger→known` 的地平線 T = 31 天，90 天可達率 86.8%。

### 4.3 reply 動力的條件產率（**0 新增成本**）

`reply` 現況 0 生產者。**本契約不新增生產端**（PD-1）。但 `cross_chat` 已存在且**已付費**：每輪 = 3 次 LLM 調用（`scheduler.py:1065-1072`），且是 **A→B→A 的雙向 3 回合**（10 筆 cross_chat 已落 `interactions.jsonl`，E4）。

→ **條件產率宣告**：`R_reply_system = R_co_system = 1.056 /day`（每個 session 折抵 1 個 reply_exchange）。此為**讀側語義釐清**（把「既有記錄中的雙向 3 回合對話」讀為 1 個 reply exchange），**不觸發任何新的 LLM 調用、不新增生產端、不新增成本**。→ `R_reply_pair = 0.02246 /pair/day`，與 `R_co_pair` 同。

> **邊界聲明**：把 `interactions.jsonl` 的 cross_chat 記錄讀為 reply 訊號，**屬於讀側聚合口徑**，不是新增生產端。真正的 `event_type="reply"`（`SOCIAL_WORLD_EVENT` 領域）**仍是 0 生產者**，列 §10 SI-3 議題。

---

## §5 新門檻表（PD-3；每一列附「為何此值在 R 下可達」）

### 5.1 升帶（**取代 SG-1 §3.3 之數值**；形態凍結，全整數）

> 計數口徑 0 變更：`objective.*` 為**累計整數**（`apply_relation_evaluation` 增量累加，`relationships.py:477-479`），非窗內瞬時值。**每次結算至多升 1 級**（0 變更）。

| 遷移 | **新門檻（整數）** | 為何此值在 R 下可達 |
|---|---|---|
| `stranger → known` | **`co_presence_sessions ≥ 1`**（或 `reply_exchanges ≥ 1`） | `P(≥1)`: 30 天 49.0% / 90 天 **86.8%**；**中位可達 ≈ 31 天**（`λ=0.693`，即本列宣告的地平線 T = 31 天）。舊值 2 的 90 天可達率 60.0%、中位 75 天（可達但慢一倍）。本條取「任一訊號即成立」，使最低帶可在**單次有效共在**後成立，並與 E2 實測 `co=1` 的 10 條對子對齊（舊表下它們永遠卡住） |
| `known → familiar` | **`co_presence_sessions ≥ 2` 且 `reply_exchanges ≥ 2`** | `P(≥2)`: 90 天 60.0% / 180 天 91.2%；**中位可達 ≈ 75 天**（`λ=1.678`）；舊值 5 需**中位 208 天 / 365 天僅 0.4%** → 重標定後可達性由 0.4% 提升到 60.0%（同地平線 90 天）。門檻由「同一 24h 窗」改為「**累計、且須落在 ≥2 個不同 24h 窗**」——這是把 E4 的結構性不可達（窗內 ≥2，`P=0.03%/天`）改為可達（跨窗累計 ≥2）。`reply≥2` 與 `co≥2` 同步（兩者同產率，§4.3），維持「雙向回應 + 重複共在」的語義，不引入單邊計數 |
| `familiar → close` | **`co_presence_sessions ≥ 4` 且 `reply_exchanges ≥ 4`** | `P(≥4)`: 180 天 57.5% / 365 天 **96.3%**；**中位可達 ≈ 164 天**（`λ=3.672`）；舊值 15 需**中位 653 天**（730 天僅 0.03%）→ 重標定後可達性由 ~0 提升到 57.5%（地平線 180 天）。**刪除 `dream` 行**（`R_dream = 0`，結構性不可達，保留即 INV-5 違規；見 §11 OQ-4） |

### 5.2 降帶（**取代 `DEMOTE_DAYS = 30`**）

| 條件 | **新值** | 為何此值一致於新門檻 |
|---|---|---|
| 連續 **> `DEMOTE_DAYS`** 天無任何新訊號（`last_signal_at` 無更新）→ 下移 1 帶 | **`DEMOTE_DAYS = 90`** | **INV-6 要求降帶門檻 ≥ 平均到達間隔**：`E[gap_pair] = 44.5 天` → 30 天門檻會在「該 pair 還沒被抽到第二次」時就降帶；90 天內「0 訊號」機率 = **13.3%**，即「因沉默而降帶」成為**少數事件**，且 90 天 `P(≥1 訊號) = 86.8%` ⇒ 多數對子在降帶前已獲新證據。**同時保留**：每次結算至多降 1 帶；底帶 `stranger` 不降；`fallback_ts = last_interaction_at`（Bryan 軸）；無訊號對子亦過降帶檢查（`relation_settlement.py:238-243`，0 變更） |
| 已在 `stranger` | 不降（底帶） | 0 變更 |

> **30 天規則的一致性說明（PD-3 要求明示）**：舊 30 天與舊門檻（窗內 `co≥2`）**本來就互相矛盾**——舊門檻下一個 pair 需 ~44.5 天才被抽到一次，30 天降帶必然在「有機會升帶前」先觸發降帶（對 `known` 以上的帶）。因此舊組合是「**只降不升**」的單向棘輪，這正是 E1/E2 之外的第二重凍結機制。新組合（90 天）與新門檻（跨窗累計）在 `R` 下互相一致：**降帶週期 = 90 天 ≈ 2.0 × 平均到達間隔（44.5 天）**。

### 5.3 與 §2 實測的直接對照（本表生效後的預期行為）

| 對象 | 實測現值 | 新表下的下一步 | 依據 |
|---|---|---|---|
| 10 條 `co=1` 的對子 | `co=1, reply=0` | 下次結算 `co` 達 1 → **升 `known`**（舊表需 2） | E2 |
| 其餘 80 條 A2A 對子 | `co=0, reply=0` | 維持 `stranger`，待抽樣 | E2 |
| Bryan 軸（10 位） | `co=0, reply=0, band=stranger` | **band 不動**（Bryan 不參與 co/reply 訊號，0 變更）；Bryan 軸的羈絆走 §6 的**獨立證據鏈** | E10 |
| `dream_exchanges` | 100/100 = 0 | 不再作為升帶依據（§11 OQ-4） | E5 |

---

## §6 TA-2 換軌規格（PD-4）

### 6.1 廢止與新增

| 項 | 處置 |
|---|---|
| `TENSION_ELIGIBILITY_MIN_CONFIDENCE = 0.5`（`temporal_phenomenology.py:50`） | **廢止**（連同 `:114` 的唯一使用點） |
| `_get_bry_confidence()`（`:124-146`） | **取代**為 `_read_bond_evidence(agent_id)`（0 浮點、0 confidence） |
| `classify_temporal_state(last_interaction_ts, now, confidence=None)` | **新簽名**：`classify_temporal_state(last_interaction_ts, now, bond_evidence=None)`。**仍不接收 `agent_id`**（INV-7 的 0 per-agent if/else 保留） |
| 三態枚舉與文案表（`:42-46,74-81`） | **0 變更**（含 T1 防線句） |
| 三格邊界 `<24h / 24h~7d / ≥7d`（`:54-55`） | **0 變更** |

### 6.2 三態完整判定式（含邊界，取代原 §3.2 資格門檻）

```
INPUT : last_interaction_ts : int   (秒; <=0 表示從未互動)
        now                 : int   (秒)
        bond                : BondEvidence | None

STEP 1  從未互動
        if last_interaction_ts <= 0            -> STATE_CALM      (無感)
STEP 2  羈絆資格（BOND GATE）—— 未取得羈絆證據即不構成張力
        if not bond.eligible                   -> STATE_CALM      (無感)
STEP 3  間隔現象化（離散三格，非連續函式）
        elapsed = now - last_interaction_ts
        if elapsed <  24*3600                  -> STATE_CALM      (無感)
        if elapsed <  7*24*3600                -> STATE_TENSION   (牽掛)
        else                                   -> STATE_RESOLVED  (釋然)
```

- **邊界釘死**：`elapsed < 86400` 無感；`86400 ≤ elapsed < 604800` 牽掛；`elapsed ≥ 604800` 釋然（與現行 `:117-121` 完全一致，0 變更）。
- **`elapsed <= 0`（未來時間戳 / 時鐘回撥）**：落 STEP 3 第一格 → 無感（保守，不編造）。
- **`bond is None`（讀取失敗）**：`eligible=False` → 無感（fail-silent，INV-8）。
- **0 張力分數、0 強度、0 中間值**（INV-1/INV-2）。

### 6.3 羈絆證據來源（authoritative source of truth）與**完整優先序**

`BondEvidence` 由 **`relationships.json` 的 `user_bryan` entry** 讀出（兩個呼叫端同源：`proxy.py:1056-1076` 與 `decision.py:498-528`），**只讀、不寫、不創建 entry**（對齊 C-3.1 §3.3 之「讀側 0 寫副作用」）：

| 序 | 判據 | 條件（**全整數／布林，0 浮點**） | 語義 |
|---|---|---|---|
| **P1** | `impression_tags` 非空 | `len([t for t in tags if t]) >= 1` | D4 真值源之一（SG-1 §2.2） |
| **P2** | `relational_band` 非 `stranger` | `band in {"known","familiar","close"}` | D4 真值源之二（SG-1 §2.2） |
| **P3** | `interaction_count ≥ 10` **且** `last_interaction_at` 為合法 ISO 時間戳 | `int(count) >= 10 and parseable(last_interaction_at)` | **Bryan 軸 fallback**（E10：最小值 31，全 10 位皆過） |
| **P4** | 其餘 | 全部落空 | `eligible = False` → 無感 |

**優先序語義**：**任一條命中即 `eligible = True`**（`OR` 串聯）。**不使用單一「最高優先」短路**——P1/P2 是 D4 原生真值，P3 是**只為 Bryan 軸存在**的客觀計數 fallback；三者互斥性不影響結果。

**明確禁止（同名不同尺度陷阱，PD-4 明令）**：
1. **禁止**把 legacy `confidence` 的 `0.5` 直接對映到 SG-2 的 `familiar`（或任何 band）。兩者不同尺度（E7）：TA-2 的 `0.5` = legacy M5.13-3 confidence band 的「熟悉」（`proxy.py:350`）；SG-2 的 `familiar` = counter band（`reply≥3 且 co≥5` → 本契約 §5 改為 `co≥2 且 reply≥2`）。
2. **禁止**任何 `confidence` 讀取進入 `classify_temporal_state` 或其證據鏈（`confidence` 仍是 SG-1 §2.3 的**只讀遺留欄位**；本契約讓 TA-2 也真正退出該欄位）。
3. **禁止**為 TA-2 建立新的持久化欄位／新 schema（TA-2 §2.2「三態不持久化」0 變更）。

### 6.4 Bryan 軸的羈絆證據 fallback 設計（PD-4 要求的現實處置）

**現實（E1/E9/E10 實測）**：Bryan 軸上 `impression_tags` **100% 空**（P1 全落空）、`relational_band` **100% `stranger`**（P2 全落空；因為 Bryan 不參與 co/reply 訊號，§5.3）→ **P1/P2 在 Bryan 軸上恆不可用**。

**因此 P3 是 Bryan 軸的設計性 fallback，且必須 fail-silent**：

| 情境 | 行為 |
|---|---|
| `user_bryan` entry 不存在 | `bond = None` → **無感**（fail-silent，不編造、不建 entry） |
| entry 存在但 `interaction_count < 10`（**待驗**：實測最小值 31，尚無 <10 的樣本） | `eligible = False` → 無感（保守） |
| `interaction_count ≥ 10` 但 `last_interaction_at` 缺失／壞值 | `eligible = False` → 無感（**不退回 `confidence`**，不退回 `created_at`） |
| `interaction_count ≥ 10` 且 `last_interaction_at` 合法 | `eligible = True` → 交由 §6.2 STEP 3 依 `elapsed` 判三態 |
| 讀取任何例外 | `bond = None` → 無感（`logger.debug`，不 raise，不阻塞 prompt） |

**A2A 軸的對稱說明**：TA-2 的三態只看 **Bryan 軸**（原設計 0 變更：`_get_bry_confidence` 即讀 `BRYAN_ENTITY_ID`）。`impression_tags` 空集合時，**沒有**「其他 agent 的 tags 可借用」的路徑——`_read_bond_evidence` 恆只查 `BRYAN_ENTITY_ID`（per-agent 隔離沿用 TA-2 §3.2）。

**保守性驗證（INV-7）**：
- 現況（E6）：`confidence < 0.5` → **10/10 恆無感**。
- 換軌後：`eligible` 對 10/10 皆 True（E10，`interaction_count ≥ 31`），故三態**實際由 `elapsed` 決定** → **會出現非無感態**。這是**換軌的目的**（消除單態化），不是 transmit 提升。
- `transmit` 率**不提高**的結構理由：TA-2 只改 prompt 的「關係時序」一行（語義情境），**不觸碰 Decision 四元、不觸碰 Motive 產生、不觸碰冷卻守則**（T2/T3/T4/T5 全部 0 變更）；且第三行**保留 T1 防線句**。

### 6.5 三態注入文本（**逐字保留，0 變更**；PD-4 明令）

| 態 | 第三行文本（`temporal_phenomenology.py:74-81` 原文） |
|---|---|
| 無感 | 「一切如常，你們的互動節奏一如往常，這並不代表需要主動聯絡。」 |
| 牽掛 | 「距離上次與 Bryan 對話已有明顯間隔，具有存在感，這份在意讓你想起過去那些對話，**但這絕不代表必須主動聯絡**。」 |
| 釋然 | 「雖然許久未聯絡，但那份珍惜仍在心中，這並不代表必須主動聯絡。」 |

- `reflect-only 加權` 語義**0 變更**：牽掛態第三行「這份在意讓你想起過去那些對話」= 情境呈現（讓 reflect 更自然），**不是指令、不加數值權重**。
- 兩個呼叫端（`proxy.py` 表達路徑 group/private、`decision.py` Relevant context 子塊）**掛載點 0 變更**。

---

## §7 `impression_tags` 寫入規範 + Privacy Gate 邊界（PD-5）

### 7.1 為何現在寫不進去（3 個獨立原因，全部實測）

1. **呼叫端不傳值**：唯一生產呼叫 `src/soul/dream_event.py:483-485` 只傳 2 個位置參數（`target_agent_id, impression`），`impression_tags` 永遠 `None` → `relationships.py:360` 的 `if impression_tags is not None` 永遠 False。**（SG-3 複驗：工單原文「唯一生產呼叫不傳 `impression_tags`」完全成立，行號正確。）**
2. **抽取器不產生 tags**：`dream_event.py:717-764` 的 `_extract_impression` 回傳**單一字串**（`return text[:30]`），管線中**根本沒有 tag 概念** → 即使補上參數，也無來源可傳。
3. **`on_dream` 的 target 永遠不是 Bryan**：`_pick_dream_target`（`dream_event.py:142-163`）取「confidence 最高的其他 agent」→ `on_dream` 的 `target_id` 恆為 peer → Bryan 軸的 `impression_tags` **設計上永不可能由夢境寫入**（E9 的 peer 40/90 非空 vs Bryan 0/10 非空，正是此結構的另一面）。

### 7.2 合法寫入管道（**白名單，唯一入口不變**）

**唯一寫入方法**：`RelationshipsStore.update_impression(other_id, impression_text, max_length=20, impression_tags=[...])`（`relationships.py:333-375`，0 簽名變更）。**禁**任何直接 dict 賦值寫 `entry["impression_tags"]`。

| # | 允許的 tag 來源（呼叫端） | 可寫 target | 詞彙表 | 節流 |
|---|---|---|---|---|
| **W1** | **agent 對 peer 的夢境印象**（既有 `dream_event.on_dream` 路徑，`dream_event.py:477-487`）→ **擴充為同時產生 tags**（在 `_extract_impression` 的同一 LLM 調用內要求結構化 tags，或由回傳文本確定性抽取）；**必須**把 `impression_tags` 傳入 `update_impression` | `other_id ∈ AGENT_IDS`（**排除 `user_bryan`**） | 開放詞彙集（見 7.4） | 沿用 `≤1 次/24h/agent`（D3；`dream` 每日 1 次，天然滿足） |
| **W2** | **A2A 公開互動印象**（未來實作掛載點：關係結算層 `settle_relations` 窗口末，僅當本窗對該 pair 有 `co_presence_sessions` 增量時） | `other_id ∈ AGENT_IDS` | 同上 | 既有 `24h/agent` 結算窗（0 新定時器） |
| **W3** | **Bryan 軸印象**（若未來需要）：**唯一**允許來源 = **公開頻道**（`lounge` / `soul_wall`）的互動事實，或**Owner 顯式授權**的來源；**必須**經 SI-2.1 防線 2（`SocialEventProducerGate`）判定為 `public` | `BRYAN_ENTITY_ID` | 同上 | 同 W1/W2 |

**明令禁止的來源（fail-closed）**：
- ❌ 與 Bryan 的 **1:1 私聊 DM 內容**（INV-9；SI-2.1 §5.1 判定表：DM 默認 `private`，嚴格攔截於廣播總線之外）。
- ❌ 任何 `impression` 欄位的**原文搬運**（`impression` 是 legacy 欄位，且其 40/90 非空值來自 LLM 自由文本，含個人化描述 → 不得升格為跨 agent 可觀測的 tag）。
- ❌ 任何未經 §7.2 白名單的第三方呼叫端（含測試以外的 ad-hoc 腳本）。

### 7.3 Privacy Gate 邊界（消費端對稱，INV-9）

| 邊界 | 規則 |
|---|---|
| 寫入端 | tag 的產生輸入**必須**可追溯到「公開可觀測事實」或「agent 自身內在活動（夢境）」。**輸入若為 `mode=="private"` / TG 1:1 / `is_private=True` 通道 → 拒絕產生 tag（fail-closed，log warning）** |
| 讀取端（既有，0 變更） | `[關係感知]` 塊（C-3.1，`proxy.py:509-569`）與 B5 他者源（`seed_provider.py:438-478`）只投影 `relational_band + impression_tags`，**不含 `impression` 原文、不含 `confidence`、不含計數**（C-3.1 §3 已定；本契約重申為邊界） |
| 跨 agent 可觀測性 | `impression_tags` 是**跨 agent 可觀測欄位**（B5 種子會把 peer 的 tags 讀進 agent 的動機素材）→ 故 1:1 私聊內容**絕不可**流入（INV-9） |
| 與防線 3（Identity Firewall）正交 | 本節只管「什麼內容能進這個欄位」（防線 2 精神）；tag 進入 agent 自身認知後如何被對待，仍由防線 3 管（**0 變更**） |

### 7.4 tag 規範（詞彙、去重、上限、時間戳）

| 項 | 規範 | 依據 |
|---|---|---|
| 格式 | **開放詞彙集**（open set），自由格式短語；**0 數值權重、0 分數、0 排序語義** | SG-1 §2.2（`impression_tags` 為 open set）；INV-1 |
| 單項長度 | **≤ 12 字符**（`proxy.py:506` `_MAX_IMPRESSION_TAG_CHARS = 12` 已是渲染端硬上限）；寫入端 `update_impression` 的 `max_length=20` 不變（0 參數變更） | C-3.1 §4.2 |
| 數量上限 | **≤ 5**（寫入端即截斷；`proxy.py:505` `_MAX_IMPRESSION_TAGS = 5` 已在讀取端截斷） | C-3.1 §4.2（token 預算 ≤80） |
| 去重 | **去重、保留首次出現序**（`dict.fromkeys` 語義）；**順序即語義**（C-3.1 §4.1「順序 = 寫入序」，讀取端取前 5） | C-3.1 §4.1/§4.2 |
| 空值 | 空串 / 純空白 / 非 `str` → **丟棄該項**；全空 → **不覆寫既有 tags**（0 假資料，對齊 `relationships.py:354-355` 的「空字串留空不寫」精神） | 既有寫入邏輯擴充 |
| 時間戳 | **不新增 schema 欄位**。新鮮度由既有欄位承載：`last_updated`（每次寫入更新）+ `last_relation_update_ref`（冪等鍵）+ `band_updated_at`（帶遷移時）。24h 節流窗天然界定 tag 新鮮度 | SG-1 §2.2 additive 欄位集 0 變更（**本契約不加欄位**；若實作期認為需要 `impression_tags_updated_at`，屬 schema 4.2→4.3 議題，**需另開工單 + Owner 裁定**，見 §11 OQ-6） |
| 大小寫／正規化 | **不做**自動大小寫轉換或詞形還原（0 語義損失風險） | No-Scoring 精神 |

> **本節只定義規範，不授權實作**（PD-5）。

---

## §8 遷移與相容（PD-6）

### 8.1 既有測試改寫清單（語意變更必須同步改寫）

| # | `file:line` | 現行斷言 | 為何必須改寫 |
|---|---|---|---|
| T1 | `tests/social/test_relational_bands.py:43-94`（`TestUpgradeTransitions` 全 class） | `evaluate_band(STRANGER, reply=1) == KNOWN`、`(KNOWN, reply=3, co=5) == FAMILIAR`、`(FAMILIAR, reply=10, co=15) == CLOSE`、`(FAMILIAR, reply=5, dream=4) == CLOSE` 等逐值斷言 | §5.1 門檻值全數重標定（`co≥1`；`co≥2 且 reply≥2`；`co≥4 且 reply≥4`）→ 既有期望值全部失效。**語意本身也變**（`close` 的 dream 行刪除） |
| T2 | `tests/social/test_relational_bands.py:124,129` | `should_demote(now - 30d) is False` / `(now - 31d) is True` | `DEMOTE_DAYS` 30 → **90**（§5.2） |
| T3 | `tests/social/test_relational_bands.py:168-169` | `assert DEMOTE_DAYS * 86400 == 2592000` | 新值 = 90 × 86400 = **7776000**（**整數斷言本身保留**，僅常數值變） |
| T4 | `tests/social/test_relational_bands.py:151-166`（`TestNoFloatGuard`） | AST 掃描 0 float 常量 + 0 `weight`/`affinity`/`score` | **不斷言值，只斷言形態 → 應保持 0 改動並繼續通過**（列入清單以便實作者確認，不是要改） |
| T5 | `tests/test_ta2_temporal_phenomenology.py:55-58` | `classify_temporal_state(LAST_2D, NOW, 0.4) == CALM`（硬斷言 **confidence 門檻行為**） | §6.1 廢止 `confidence` 參數 → 整組呼叫簽名與語意失效（0.4 已非輸入） |
| T6 | `tests/test_ta2_temporal_phenomenology.py:61-65` | `(LAST_3H, NOW, 0.8) == CALM` | 同上（第 3 位置參數換為 `bond_evidence`） |
| T7 | `tests/test_ta2_temporal_phenomenology.py:67-71` | `(LAST_2D, NOW, 0.8) == TENSION` | 同上 |
| T8 | `tests/test_ta2_temporal_phenomenology.py:73-77` | `(LAST_10D, NOW, 0.8) == RESOLVED` | 同上 |
| T9 | `tests/test_ta2_temporal_phenomenology.py:79-85` | 三態集合斷言（傳 0.8） | 同上；**新的可達性驗證**應改以「合格羈絆 + 三個 `elapsed` 格」為輸入 |
| T10 | `tests/test_ta2_temporal_phenomenology.py:87-93` | 「資格判定非強度公式」：`0.5` 與 `0.9` 同三態 | **語意需重寫為 band 版**：`bond_evidence` 的**程度差異不影響三態**（`interaction_count=31` 與 `=385` 同結果）——這是 INV-2 的新形式 |
| T11 | `tests/test_ta2_temporal_phenomenology.py:95-99` | 「無 per-agent if/else」：`(LAST_2D, NOW, 0.8) == TENSION` | 同上（簽名變更）；**斷言意圖必須保留**（INV-7） |
| T12 | `tests/test_ta2_temporal_phenomenology.py:105-107` | `patch.object(tp, "_get_bry_confidence", return_value=confidence)` | `_get_bry_confidence` 被 `_read_bond_evidence` 取代 → patch 目標消失（`AttributeError`） |
| T13 | `tests/test_ta2_temporal_phenomenology.py:151-155` | 釋然態文案「珍惜仍在心中」（走 `confidence=0.8`） | 前提（`confidence` 路徑）失效；**文案斷言本身 0 變更**，需改注入 `bond_evidence` |
| T14 | `tests/test_ta2_temporal_phenomenology.py:157-162` | `test_fail_silent`：patch `_get_bry_confidence` `side_effect=Exception` → `""` | 同上；**fail-silent 意圖必須保留**（INV-8），改 patch 新函式 |
| T15 | `tests/test_ta2_temporal_phenomenology.py:194,223` | `patch.object(tp, "_get_bry_confidence", return_value=0.8)`（proxy 注入測試的 2 處前置） | 同上（patch 目標消失） |
| T16 | `tests/test_ta2_temporal_phenomenology.py:311-363`（`test_decide_motive_generates_anchor`） | 寫入 4.1 entry（`confidence: 0.8`、`last_interaction_at` 2026-08-31、`interaction_count: 10`）→ 期望 prompt 含 `[TEMPORAL ANCHOR]` | entry 缺 `relational_band` / `impression_tags` → 走 P3 fallback（`count=10 ≥ 10` 恰好過線）→ **測試仍可能通過，但語意已變**（不再因 `confidence` 而注入）；**應改為顯式測 P1/P2/P3 三條路徑** |
| T17 | `tests/harness/test_goal_driven_harness.py:204` | `monkeypatch.setattr(dec_mod, "_build_temporal_anchor", lambda *a, **k: None)` | 該 monkeypatch 假設 `decision._build_temporal_anchor(agent_id)` 存在。§6 只在 `temporal_phenomenology` 內換軌、**不刪 `decision._build_temporal_anchor`**（掛載點 0 變更）→ **本項應 0 改動並繼續通過**；列入清單僅為「若實作期改動 `decision.py` 讀側（改讀 band/tags 而非 confidence）則必須同步本項」，並附**為何**：此測試是 Decision 路徑的隔離子塊，讀側欄位語意變更會影響其 fixture 假設 |

> **補充（非「必須改寫」但需複驗）**：`tests/social/test_sg2_guardrails.py`、`tests/social/test_relationship_store.py`、`tests/goals/test_sg2_b5_relation_seed.py`、`tests/test_c31_relational_expression.py`、`tests/harness/test_tl10_relational_expression.py`、`harness/tl9.py` 的 fixture 皆為**自建 entry + 直接傳入計數**，不讀 §5 的門檻常量值（除 `test_relationship_store.py` 的慢爬／升帶用例需逐條複驗是否踩到新門檻）。**複驗指令**：`.\.venv\Scripts\python.exe -m pytest tests/social tests/goals/test_sg2_b5_relation_seed.py tests/test_ta2_temporal_phenomenology.py tests/test_c31_relational_expression.py tests/harness/test_tl10_relational_expression.py -v`

### 8.2 資料遷移政策（**明確回答，不含糊**）

| 資料 | 政策 | 理由 |
|---|---|---|
| 既有 **100 條 entry 的 `relational_band`** | **不重算、不重寫、不前饋（0 遷移）** | 三者都會是「重算後仍全為 `stranger`」的空操作：`reply=0`（100/100）、`dream=0`（100/100）、`co ∈ {0,1}`（E2）→ 新表下僅 `co=1` 的 10 條在**下一次結算**時升 `known`。**重算＝把同一件事做兩次，且會偽造 `band_updated_at`**。正確行為 = 讓既有 `settle_relations`（30s wake，24h 節流）在下一個結算窗自然套用新門檻 |
| **`band_updated_at`（schema 4.2 設計欄位，從未寫入）** | **不補寫（0 回填）**。維持「只在真實發生帶遷移時寫入」（`relationships.py:498,527`） | 回填 `now` 或 `last_interaction_at` 會讓「從未遷移」看起來像「遷移過」，**汙染可審計性**（E1 的「0 筆存在」本身是重要證據）。`objective.last_signal_at` 亦全為空 → 無合法回填源。**實測無任何 entry 需要此欄位才能運作**（全部讀側用 `.get(..., "stranger")` 缺省） |
| 既有 `objective.*` 計數 | **0 遷移**（累計語義（`relationships.py:477-479`）不變） | §5 只改「讀這些計數的門檻」，不改寫入 |
| 既有 `confidence`（含 4 條非零、`max=0.0979`） | **0 遷移、0 重算、0 歸零**（只讀遺留欄位，SG-1 §2.3 已定；本契約使其在 TA-2 也退出） | D4 已在該欄位上凍結；本契約 §6.4 明令**禁止**「退回 confidence」即已足夠 |
| 既有 `impression`（peer 40/90 非空、Bryan 10/10 空） | **0 遷移**（不搬入 `impression_tags`、不清空） | 搬運 = 把 legacy LLM 自由文本升格為跨 agent 可觀測 tag，違 §7.2 禁令 2 與 INV-9 精神。**40 條非空 `impression` 不構成任何羈絆證據**（§6.3 只讀 `impression_tags` + `band` + `interaction_count` + `last_interaction_at`）→ 見 §11 OQ-1（是否要另設設計性 fallback，**需 Owner 裁定**） |
| `impression_tags` | **0 回填**（維持 100/100 空，待 §7 管道首次寫入） | 無合法來源可回填（§7.1 的三個原因） |

### 8.3 回滾方案

| 情境 | 回滾動作 | 資料影響 |
|---|---|---|
| 新門檻造成非預期升帶 | **revert 新門檻 commit**（`src/social/relational_bands.py` 的 4 個常量 + `src/soul/temporal_phenomenology.py` 的換軌）→ 恢復舊值（`0/2`、`3/5`、`10/15`+dream 行、`DEMOTE_DAYS=30`） | **0 資料遷移**（§8.2 已保證 schema 與既有 band 值皆 0 變更）→ 回滾 = **純 code revert**，可任一 commit 粒度執行 |
| 新門檻造成非預期降帶 | 同上；**已寫入的降帶值不自動回復**（`evaluate_band` 只升不降，`relational_bands.py:115`）→ 由後續結算窗（新訊號）自然回升（SG-2.1 語義：需窗口內新訊號） | 若需強制回復，須**另開資料修復工單**（不在本契約授權範圍） |
| TI-2 換軌造成非預期三態分佈 | **revert 換軌 commit** → 恢復 `confidence` 閘門（= 恢復 10/10 無感） | 0 資料影響（三態**不持久化**，TA-2 §2.2；每次現算） |
| `impression_tags` 寫入異常 | **revert 呼叫端**（`dream_event.py` 的 tags 傳遞）→ tags 停止累積，既有值留在檔案中（讀側 0 破壞） | 0 schema 影響 |

**回滾不變量**：因本契約**不新增／不修改任何資料欄位**，所有回滾皆為 code revert，**無需資料回滾腳本**。

---

## §9 明確不授權／不做清單（PD-7）

### 9.1 Frozen Contract：**不改什麼**（逐條打勾）

| # | Frozen 觸點 | SG-3 狀態 |
|---|---|---|
| 1 | Agency 4 stages（`src/agency/stages.py`） | ✅ 0 變更 |
| 2 | TriggerEnvelope（`src/agency/trigger.py`） | ✅ 0 變更 |
| 3 | InnerLifeEvent（`src/inner_life/event.py`） | ✅ 0 變更 |
| 4 | 4 handlers | ✅ 0 變更 |
| 5 | SAGE 寫入邏輯（`src/memory/sage/writer.py` + `GraphStore.add_fact`） | ✅ 0 變更（`impression_tags` 0 SAGE facts、0 昇華鏈） |
| 6 | SubmissionGate（5 步鏈 + 第 6 步 Identity Firewall） | ✅ 0 變更 |
| 7 | **SI-2.1 三道防線**（Privacy Visibility Gate / ProducerGate / Identity Firewall） | ✅ 0 變更（§7.3 只是**重申**私聊邊界，不新增也不放寬 ProducerGate 判定） |
| 8 | **No-Scoring 整數門檻剛線** | ✅ 0 放寬（INV-1；AST 斷言保留） |
| 9 | schema 4.2 欄位集與寫路徑（`relationships.json`） | ✅ 0 變更（0 新欄位、0 新檔、0 新表） |
| 10 | `Motive.target` 值域 / Decision 四元 / DECISION-PROMPT 主文本 | ✅ 0 變更 |
| 11 | 30s wake 掛載點與 `settle_relations` 節流語義 | ✅ 0 變更 |

### 9.2 本契約**不授權**的事項

1. ❌ **不授權實作**：本契約是設計輸入，不是施工授權。**未經主大腦 + Owner 另開實作工單，不得改任何 `src/**`、`tests/**`、`configs/**`、門檻常量、生產資料。**
2. ❌ **不授權新增生產端**：不得新增 `SOCIAL_WORLD_EVENT` 的 publish 端（PD-1）。
3. ❌ **不授權提高互動生產頻率**：不得調整 `cross_chat / shared_event` 的 6–12h 間隔（PD-1；提高頻率 = 增加真實 LLM 調用 = **花錢事項**，未經 Owner 核准）。
4. ❌ **不授權 schema 演進**：不得新增 `impression_tags_updated_at` 或任何新欄位（§7.4；屬 4.2→4.3，需另案）。
5. ❌ **不授權改 `confidence`**：不得寫入、不得歸零、不得從檔案刪除（只讀遺留欄位）。
6. ❌ **不授權改降帶的「只升不降」結構**：`evaluate_band` 仍只升不降；`demote_band` 仍只降。
7. ❌ **不授權動既有 Data**：0 回填、0 重算、0 清空（§8.2）。
8. ❌ **不授權與 P1-1 決策 trace 工單互相干涉**：`src/soul/decision_trace.py:17` 明文自述「不碰任何門檻常數（`TENSION_ELIGIBILITY_MIN_CONFIDENCE` / `relational_bands` / `CONFIDENCE_*`）」（該檔屬 `e6eefd6` P1-1 觀測面）。**兩條線在同一批文件層面相交，但程式面必須保持正交**：P1-1 只增加觀測（落盤 trace），SG-3 只改判定門檻與證據來源；**任一工單實作時必須複驗另一邊的常量未被誤改**。

---

## §10 未來接口：SI-3（新增生產端）前置條件（PD-1）

> **現況定位**：`SOCIAL_WORLD_EVENT` 已有完整的 **schema（`eventbus/schema.py:61,337`）+ 訂閱端（`world/middleware.py:63,259-287,354`）+ 防線（`social/producer_gate.py`、`social/identity_firewall.py`、SubmissionGate 第 6 步）**，**獨缺生產端**（0 個 publish）。這是 SI-2.2 的已知缺口，不是本契約的範圍。

**SI-3（未來工單）的前置條件（全部滿足才可開工）**：

| # | 前置條件 |
|---|---|
| SI-3-a | **Owner 核准**：新增對外廣播生產端＝跨越 SI-2.1 防線（隱私），屬高風險事項，**須 Owner 明示核准**（本契約不代為核准） |
| SI-3-b | **必經 `SocialEventProducerGate.evaluate(channel_mode, channel, explicit_public)`**，且判定表照 SI-2.1 §5.1（`private` → BLOCK，fail-closed；無法判定 → BLOCK） |
| SI-3-c | **0 新增 LLM 調用成本**：生產端只能**包裝既有已付費事件**（例如把既有 `cross_chat` / `shared_event` 的公開段升級為 `SOCIAL_WORLD_EVENT`），**不得**為了產生社交事件而新增 LLM 調用 |
| SI-3-d | **可達性再宣告（INV-5 的必然要求）**：生產端上線後，`R_co_pair` / `R_reply_pair` 必然改變 → **必須重算 §4.2 可達性表並重新宣告 `(R, T, P)`**；若新門檻不再在 T 內可達 → **同步重標定 §5 門檻**（否則違反 INV-5） |
| SI-3-e | **Identity Firewall 不變**：`actor_id != current_agent_id` → `EXTERNAL_OTHER_ACTION` 三不變量 0 變更（防線 3） |
| SI-3-f | **0 新投遞通道**：仍不得建 agent→agent 私聊 DM（SG-1 §5.3 0 變更） |
| SI-3-g | **本契約 §5 門檻可回退**：SI-3 上線後若產率上升，允許把 §5 門檻**回調較嚴值**（形態凍結不變），但**必須**留下新的可達性推導 |

**Bryan 軸的未來接口（同列 SI-3 議題，本契約不授權）**：若未來要讓 Bryan 軸的 `relational_band` 也能演進，需先定義「Bryan 軸的客觀訊號來源」（現況 `objective.*` 對 Bryan 恆 0，因為 Bryan 不參與 `_fire_shared_event` / `_fire_cross_chat` 的 pair 抽樣），並穿越防線 2。**本契約不授權**。

---

## §11 待決事項（Open Questions）

> 以下 **全部標明「需 Owner 裁定」**。**不以猜測填空**；未裁定前，各項以「**維持現狀（不改）**」為預設。

| # | Open Question | 為什麼需要 Owner 裁定 | 未裁定時的預設 |
|---|---|---|---|
| **OQ-1** | **legacy `impression`（peer 40/90 非空）是否升格為羈絆證據或遷入 `impression_tags`？** | 三難：(a) 不處理 → 40 條對子在 §6.3 下**失去羈絆證據**（若未來 A2A 軸也要跑三態）；(b) 升格為**證據**（不搬欄位）→ 使 legacy 欄位重新參與判定，與 D4「band + tags 為唯一真值」有張力；(c) 搬入 `impression_tags` → 把 LLM 自由文本（可能含個人化描述）升格為**跨 agent 可觀測欄位**，觸 §7.2 禁令與 INV-9 風險。**三者代價不同，屬價值判斷** | **維持現狀**：不處理、不搬運、不升格（§8.2）。Bryan 軸不受影響（Bryan 的 `impression` 本來就 10/10 空） |
| **OQ-2** | **`confidence` 的 per-touch 衰減是否真的是設計意圖？** | `touch` 每次 `+0.02`（`relationships.py:63`）但 `_decay_locked` 以 `last_interaction_at` 為 anchor 每次讀寫即衰減 `days*0.02`（`:272-276`）→ **任何 ≥1 天的互動間隔淨增益 ≤ 0，且非負下界把值釘在 0.0**（實測 `max=0.0979`、`10/10 Bryan 軸 7 位為 0.0`）。註解自述「0.02/天對稱 0.02/次 → 30 觸發到 0.9」，**與實際行為不符**。這是 **M5.13-x 領域的既有行為**，D4 已凍結（本契約 0 變更），但若 Owner 認為衰減語意有誤，需另開工單 | **維持現狀**（本契約 0 觸碰）。§6.4 已使 TA-2 完全退出此欄位，因此**即使衰減語意有誤也不再影響三態可達性** |
| **OQ-3** | **`band_updated_at` 是否要建立可審計的遷移歷史？** | 現況該欄位只在「帶值實際變化」時寫入（且 100/100 從未寫入過）→ **無帶遷移歷史可查**（無法回答「這個對子是什麼時候從 known 掉回 stranger 的」）。若要可審計，需新增 append-only 遷移 trace（**新檔／新欄位 → schema 議題**）。屬可觀測性投資 | **維持現狀**：0 回填、0 新欄位（§8.2）。帶遷移目前只有 `logger.info`（`relation_settlement.py:255-262`）可事後追 |
| **OQ-4** | **`familiar → close` 刪除 `dream` 行是否核准？** | §5.1 刪除 `(dream≥4 且 reply≥5)` 行的理由是「`R_dream = 0`，保留即 INV-5 違規」。但 `dream_exchanges` 門是 SG-1 §3.3 明文契定的**設計保留**（`relation_settlement.py:16-18` 註明「門保留待未來載體」）→ 刪門＝縮減 `close` 的證據面（未來若有 dream 載體需重新加回） | **依 §5.1 執行刪除**（不可達門檻 = INV-5 違規）；但**明文保留「門」的未來接口**：若 SI-3 或後續工單補上 dream 方向性載體，可重新加入 `(dream_exchanges ≥ 4 且 reply_exchanges ≥ 4)` 行 |
| **OQ-5** | **`decision.py` / `dream_event.py` 的 `confidence` 讀側耦合是否要處置？** | `_build_relationship_summary`（`decision.py:416-417`）把 `confidence` 渲染成「信任度：0.00」進 Decision prompt（**讀為真值**，生產此時恆 0.00）；`_pick_dream_target`（`dream_event.py:154-163`）用 `confidence`（缺省 0.3）排序挑夢境對象。兩者皆在 D4「只讀遺留」字面內，但與「唯一真值 = band + tags」精神不符。**範圍與風險評估需 Owner 裁定** | **維持現狀**（本契約 0 觸碰；§1.3 已誠實記載為漂移） |
| **OQ-6** | **`impression_tags` 是否需要獨立的寫入時間戳欄位？** | §7.4 選擇「不新增欄位」（靠 `last_updated` + `last_relation_update_ref` + 24h 節流界定新鮮度）。若實作期認為需要 `impression_tags_updated_at`，屬 **schema 4.2 → 4.3**，需 Owner 裁定（D1 已把 schema 定在 4.2） | **維持現狀**：不加欄位 |

---

## §12 本契約的自我邊界聲明

- 本契約**只讀**程式碼與既有文件，**唯一產出** `docs/SG-3-RELATIONAL-CONTRACT.md`。
- **0 `src/**` 改動、0 `clients/**`、0 `personas/**`、0 `configs/**`、0 `tests/**` 改動、0 門檻常數改動、0 生產資料改動。**
- **0 服務重啟**：全程對 `:8000` / `:8765` / `:8766` / `:8767` 只做 `Get-NetTCPConnection -State Listen` 讀取與 pid 核對，未 kill / restart / 干擾。
- **0 commit / 0 `git add` / 0 push**；未動 `logs/ENGINEERING_STATE.md`（收尾由後續工單處理）。
- 所有引用數字皆來自實讀檔案或前序已驗證事實；無法查證者標 **NO DATA / 待驗**（§2）。
- **本契約不自行生效**：只授權未來的實作工單，實作前需主大腦 + Owner 確認（§9.2.1）。

### 附錄：關鍵證據索引

| 證據 | 位置 |
|---|---|
| 四帶枚舉 / 升帶門檻 / 降帶門檻 | `src/social/relational_bands.py:36-49,62-83,108-146,153-190` |
| 關係帶寫入與冪等 / 0 confidence 運算 | `src/soul/relationships.py:416-536`（`apply_relation_evaluation`） |
| `confidence` 常量 / 衰減 anchor | `src/soul/relationships.py:57-69,212-283` |
| `update_impression` + `impression_tags` 參數 | `src/soul/relationships.py:333-375`（`:338` 參數、`:360-369` 寫入） |
| TA-2 資格閘門（唯一使用點） | `src/soul/temporal_phenomenology.py:50,114` |
| TA-2 三態判定 / 邊界 / 文案表 | `src/soul/temporal_phenomenology.py:42-46,54-55,74-81,112-121` |
| TA-2 兩個呼叫端 | `src/llm/proxy.py:1056-1076`（+注入 `:767,1428`）、`src/soul/decision.py:498-528`（+使用 `:596`） |
| legacy confidence band 常量（同名不同尺度） | `src/llm/proxy.py:347-352`（`_RELATIONSHIP_BAND_FAMILIAR = 0.5`） |
| `on_dream` 唯一 impression 呼叫點（不傳 tags） | `src/soul/dream_event.py:477-487`（`:483-485`） |
| `_extract_impression`（只回字串，無 tag 概念） | `src/soul/dream_event.py:717-764` |
| 夢境 target 選擇（永遠非 Bryan） | `src/soul/dream_event.py:142-163` |
| 關係沉澱層（reply/co/dream 口徑） | `src/social/relation_settlement.py:10-18,91-154,157-273` |
| pair 抽樣（6–12h × 2 隻） | `src/soul/scheduler.py:160-163,1007,1058` |
| C-3.1 `[關係感知]` 渲染（tag 上限/順序/隱私邊界） | `src/llm/proxy.py:486-569`（`:505-506` 上限、`:561-569` 渲染） |
| B5 他者源（tags 共同空集合） | `src/goals/seed_provider.py:438-478` |
| SI-2.1 防線 2 判定表 | `docs/SOCIAL-DIFFUSION-CONTRACT.md:230-264` |
| SG-1 門檻表（被本契約 §5 取代） | `docs/SG-1-SOCIAL-GRAPH-CONTRACT.md:117-132` |
| TA-2 資格門檻（被本契約 §6 取代） | `docs/TEMPORAL-PHENOMENOLOGY.md:112-121` |
| 決策四元 / `_build_relationship_summary` 漂移 | `src/soul/decision.py:385-424`（`:416-417`） |
| 既有測試（整數 AST 斷言） | `tests/social/test_relational_bands.py:151-169` |
| 既有測試（TA-2 confidence 硬斷言） | `tests/test_ta2_temporal_phenomenology.py:48-99,105-107,150-162,194,223,311-363` |
| 既有測試（Decision anchor monkeypatch） | `tests/harness/test_goal_driven_harness.py:204` |
