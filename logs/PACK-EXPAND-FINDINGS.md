# PACK-EXPAND — pack 擴充（第二來源）量測與結論

- 工單：`PACK-EXPAND`（Owner 核准「pack 擴充（第二來源）」票）
- 執行者分支：`main`，基準 HEAD `48ade60`
- 量測時刻：**2026-10-22T01:23:25Z**（`now_epoch=1792632205`），工作機時區 EDT
- 本票第一目的：**先量清楚 pack 對誰有用、對誰無效**，再決定要不要寫 pack

---

## 0. 一句話結論

> **pack 擴充對 production 的實際覆蓋率會從 `1/10 隻（10.0%）` 變成 `2/10 隻（20.0%）`。**
>
> 分子＝核心 10 隻生產 agent 中「**有 pack 且該 pack 能被載入器讀取、並在生產路徑上被消費**」的 agent 數；
> 分母＝核心 10 隻生產 agent（`configs/default.yaml` 前 10 條 seeded agent）；單位＝隻（agent 數）。
> 本票前 `1`（`agent_rem`）→ 本票後 `2`（`agent_rem` ＋ `agent_ram`）。
>
> **而「現代基底 8 隻」的覆蓋率恆為 `0/8 = 0%`，且這是結構性 0、非本票能改**（見 §4、§5）。
> 若以全庫 34 隻註冊角色為分母：覆蓋率 `2/34 = 5.9%`。
>
> **這張票的價值主要在診斷，不在覆蓋**：覆蓋率只 +1 隻（+10 個百分點），
> 但它把「9 隻 modern ⇒ 補 pack 結構性無效」這條**流傳錯誤**證成了
> 「**8 隻 modern ＋ 2 隻非現代，其中 1 隻非現代**（`agent_ram`）**一直沒 pack**」，
> 並另外找出 3 個既有缺陷（§7）、4 隻本票範圍外的異世界候選（§9.3）。

---

## 1. 量測方法與紀律

### 1.1 量測口徑

| 項目 | 做法 | 來源 |
|---|---|---|
| agent 名單 | `configs/default.yaml` 的**前 10 條** seeded agent（yua/ruka/akane/rem/ram/mahiru/anna/mai/miku/aoi）；第 11 條起為 LAND-0 Batch 1 的 24 隻（`configs/default.yaml:93`） | `configs/default.yaml` |
| `civilization_base` | 逐隻 persona 的**世界觀載體**（不是角色名字）計數判定；D3 白名單的註解也逐隻標了 persona 行號 | `personas/agent_<id>.md`；`src/memory/sage/horizon.py:41-69` |
| 有無 pack | **loader 實際解析結果**（`load_epistemic_pack`），不是「目錄裡有沒有檔」 | `src/inner_life/epistemic_appraisal.py:258` |
| V4 是否 bypass | 以 `_build_pack` 對同一 agent_id 分別餵 `modern_earth` / `non_modern`，看回不回 `InstancePack`；另做字面值探測 | `src/inner_life/epistemic_appraisal.py:313-315` |
| 張力窗 | **真實** `last_interaction_ts`（生產來源）＋ **真實** bond 證據 → `classify_temporal_state` → 再跑 `collect_tensions` | `src/llm/proxy.py:1105`、`src/soul/temporal_phenomenology.py:140` |

### 1.2 真實 `last_interaction_ts` 的取得（★ 假零陷阱已迴避）

生產的來源是 **memory DB 的 `messages` 表**（`src/llm/proxy.py:4377` → `_get_last_interaction_ts`），
不是 `relationships.json`。本票的量測腳本（`%TEMP%\pack_measure\measure.py`）：

1. **先把 `data/memory.db` 複製到 `%TEMP%` 再讀**（`sqlite` `mode=ro`）⇒ 對生產 0 checkpoint 副作用；
2. 以**與生產逐行相同**的邏輯重算 `_get_last_interaction_ts`（`session_id` 後綴 `_ <agent_id>`、
   `role='user' AND speaker='bryan'` 與 `role='assistant' AND speaker=agent_id` 兩路取 max）；
3. 交叉驗證 `data/soul/<agent>/relationships.json` 的 `others.user_bryan.last_interaction_at`
   （注意 entity key 是 **`user_bryan`** 不是 `bryan`；`src/soul/relationships.py:54`）。

**★ 量測紀律自證（對照組）**：同一支腳本另外跑了「漏餵 `last_interaction_ts`」的對照組。

| 對照組 | 結果 |
|---|---|
| 漏餵（預設 `0`） | **10/10 隻 0 張力**、10/10 隻三態＝`无感`（elapsed ≈ 56.8 年） |
| 餵真實值 | **1/10 隻有張力**（`agent_rem`）、1/10 隻在張力窗內 |

⇒ 若照預設 `0` 量，會得到「**10/10 隻零張力、0 隻在張力窗內**」的**反向結論**。
（細節修正：三態不是恆為 `释然`，而是 `classify_temporal_state` 的 **STEP 1**
`last_interaction_ts <= 0 → 无感` 先短路，`src/soul/temporal_phenomenology.py:166-167`；
結論一樣是 0，但狀態值不同。）

### 1.3 唯讀保證

- 全程 **0 寫入 `data/**`**、0 改檔名、0 刪檔；
- sqlite 一律先複製到 `%TEMP%` 再讀；
- **0 啟動／停止任何服務、0 對任何 PID 下指令**；
- 未執行任何 `scripts/manual_*.py`。

---

## 2. 10 隻逐隻量測表

> 量測時刻 2026-10-22T01:23:25Z。`elapsed`＝`now − last_interaction_ts`（生產來源）。
> 「張力窗」＝`86400 ≤ elapsed < 604800` **且** bond 可用（`src/soul/temporal_phenomenology.py:166-170`、§6.2 STEP 2/3）。
> 「有無 pack」欄＝**本票改動後**的狀態（第 5 列為本票新增者，以「本票新增」標示）。

| # | agent_id | civilization_base（persona 依據） | V4 是否 bypass | 有無 pack（loader 實測） | 有 pack 時觸發哪條路徑 | 張力窗內？ |
|---|---|---|---|---|---|---|
| 1 | `agent_yua` | **modern_earth**（原創都市；persona 僅「加班」一項現代載體） | **是** | 無 | S0→`None`（無 pack）＋ D3 白名單二次旁路 | ❌ `无感` 0.031 天 |
| 2 | `agent_ruka` | **modern_earth**（手機／電話／網路） | **是** | 無 | 同上 | ❌ `无感` 0.027 天 |
| 3 | `agent_akane` | **modern_earth**（現代演藝圈；`horizon.py:47`） | **是** | 無 | 同上 | ❌ `释然` 29.891 天 |
| 4 | `agent_rem` | **non_modern**（公館／白鯨／村莊／鬼族／詛咒／魔力／魔女教，31 項異世界載體） | **否** | **有**：`roswaal_mansion_v1` | S0 通過 → S2~S7 → `DeltaRecord` → **注入 `[本體參照]`**（`proxy.py:836`／`:1661`）＋ 通道 2 張力 | ✅ **`牵挂` 4.947 天**（唯一在窗內） |
| 5 | `agent_ram` | **non_modern**（公館／鬼族／福音書／魔力／魔女教，10 項異世界載體） | **否** | **本票新增** `roswaal_mansion_ram_v1` | 同上（新增後） | ❌ `释然` 35.971 天 |
| 6 | `agent_mahiru` | **modern_earth**（公園／秋千／傘／粥／打掃房間／聖誕燈／神社／初詣／模特兒，17 項現代載體） | **是** | 無 | S0→`None`（**只有 V4 一道旁路**；D3 白名單未含她 ⇒ 見 §9.1） | ❌ `无感` 0.027 天 |
| 7 | `agent_anna` | **modern_earth**（學校／神社／模特兒） | **是** | 無 | S0→`None` ＋ D3 白名單 | ❌ `释然` 75.338 天 |
| 8 | `agent_mai` | **modern_earth**（公寓／電影／醫院） | **是** | 無 | 同上 | ❌ `释然` 29.882 天 |
| 9 | `agent_miku` | **modern_earth**（現代東京；`horizon.py:51`） | **是** | 無 | 同上 | ❌ `释然` 77.034 天 |
| 10 | `agent_aoi` | **modern_earth**（現代校園；`horizon.py:50`） | **是** | 無 | 同上 | ❌ `释然` 77.054 天 |

### 2.1 逐欄合計

| 指標 | 分子/分母 | 值 | 來源 |
|---|---|---|---|
| 有 pack 的核心 agent | 1 / 10 | `agent_rem` | `configs/packs/` 實測（改動前只有 1 個檔）＋ loader 實測 |
| **V4 會作廢的基底 | 8 / 10** | 現代基底 | persona 世界觀載體實測 |
| V4 不作廢的基底 | 2 / 10 | `agent_rem`、`agent_ram` | 同上 |
| bond 證據可用 | **10 / 10** | 全部 eligible（7 隻 P2_relational_band、3 隻 P3_interaction_count） | `epistemic_action.py:915-949` |
| **張力窗內** | **1 / 10** | `agent_rem`（4.947 天） | `classify_temporal_state` 實測 |
| 產生張力 | 1 / 10（1 條 `relational_tension`） | 其餘 9 隻 0 條 | `collect_tensions` 實測 |
| 通道 1（epistemic gap）張力 | 排程路徑（`utterance=""`）下 **0 / 10**；**有 pack 的 2 隻（rem、ram）在對話路徑上會產生**（實測見 §7.3） | — | `epistemic_action.py:1010-1049` |

### 2.2 交叉驗證：`relationships.json` 落後情況

| agent | DB `last_interaction_ts`（生產） | `relationships.json` | 差 |
|---|---|---|---|
| `agent_mahiru` | 1792629863（2026-10-22T01:24Z，**量測當下正在對話**） | 2026-09-22T11:46Z | **落後 ≈ 29.5 天** |
| 其餘 9 隻 | — | — | 1 ~ 303 秒（秒級／分鐘級落差，正常） |

⇒ **生產以 DB 為準**（`proxy.py:4377`）；`relationships.json` 對 `agent_mahiru` 已落後 29.5 天。
**若拿 `relationships.json` 當基準，會把 `agent_mahiru` 誤判成「30 天沒互動」。**

### 2.3 重測複核（可重現性）

同一支量測腳本於 **2026-10-22T01:50:06Z** 重跑一次，結構性結論**逐項一致**：

| 指標 | 第一次（01:23:25Z） | 第二次（01:50:06Z） | 一致 |
|---|---|---|---|
| `configs/packs/` 檔數 | 1（改動前）→ 2（本票新增後） | 2 | ✅ |
| V4 字面值探測 | `modern_earth` 拒；`non_modern`／`modern`／`""`／`Modern_Earth` 皆接受 | 同 | ✅ |
| 張力窗內的 agent | 1（`agent_rem`） | 1（`agent_rem`） | ✅ |
| 有 pack 的 agent | 2（rem、ram） | 2（rem、ram） | ✅ |
| 漏餵參數的對照組 | 10/10 隻 0 張力 | 10/10 隻 0 張力 | ✅ |
| `agent_mahiru` 的基底 | modern（persona 17 項現代載體） | 同 | ✅ |

（`elapsed` 的絕對值會隨時間推移與即時對話而微幅變動——`agent_yua`／`agent_ruka`／`agent_mahiru`
當時正在被對話——但**分佈與結論不變**。）

---

## 3. 分支判定

> 工單原設想是二分法（分支 A＝懷疑成立／分支 B＝懷疑不成立）。
> **實測結果是第三種情形：懷疑的「機制」成立，但「對象範圍」錯了。**

| 工單假設 | 實測 | 判定 |
|---|---|---|
| 「9 隻 modern 角色被 V4 bypass」 | **8 隻**被 V4 bypass（7 隻 D3 白名單 ＋ `agent_mahiru` 依 persona 判定） | ❌ **數量誤記（9 → 8）** |
| 「V4 對 modern_earth 直接回 `None`」 | **成立**，且**只擋這一個字面值**（`non_modern` / `modern` / `""` / `Modern_Earth` 全部照樣載入） | ✅ **機制成立** |
| 「那 9 隻補 pack 是結構性無效」 | 對**其中 8 隻**成立；但**第 9 隻 `agent_ram` 是 non_modern，不被 V4 擋，且原本完全沒有 pack** | ⚠️ **結論只對 8 隻成立** |
| 「pack 對 10 隻全無用」 | **不成立**：`agent_ram` 是 non_modern、不在 D3 白名單、有大量生產流量（memory DB **9,348** 筆，10 隻中最多） | ❌ |

### 3.1 分支決定（依實測）

**主體＝分支 A（V4 懷疑成立）：不為 8 隻現代基底角色寫 pack** —— 那會是無人會讀的無用功（工單明令禁止）。
**但分支 A 的結論套不到 `agent_ram` 身上**，因此依分支 B 的作法（persona 推導、loader 測試、變異實驗）
**為 `agent_ram` 補上唯一一份缺包**，並保留分支 A 要求的 V4 意圖分析與選項（§4、§5、§6）。

**旁證（不是本票的理由，但是強力支持）**：契約 §1.1 明寫解耦架構的目的就是
「加入第二位異世界角色（例：**`agent_ram` 拉姆**）時必須複製一份算子 → 邏輯分叉，行為漂移」
（`docs/EH-4-EPISTEMIC-MIND-CONTRACT.md:51`）⇒ **`agent_ram` 是架構本身預留的第二位 pack 消費端。**

### 3.2 產出摘要

| 項目 | 本票前 | 本票後 |
|---|---|---|
| `configs/packs/*.yaml` | 1 份（`agent_rem`） | **2 份**（`agent_rem` ＋ 新增 `agent_ram`） |
| 核心 10 隻的 pack 覆蓋率 | 1/10（10.0%） | **2/10（20.0%）** |
| 現代基底 8 隻的 pack 覆蓋率 | 0/8（0%） | **0/8（0%）**（結構性） |
| 全庫 34 隻的 pack 覆蓋率 | 1/34（2.9%） | **2/34（5.9%）** |

---

## 4. V4 規則的設計意圖分析

### 4.1 規則本體

```python
# src/inner_life/epistemic_appraisal.py:313-315
# V4: modern_earth → 直接 None（不視為錯誤；D3 bypass 的資料側表達）
if civ == "modern_earth":
    return None
```

### 4.2 意圖證據鏈（全部來自契約原文，非推測）

| # | 證據 | 出處 |
|---|---|---|
| 1 | 「`civilization_base: "non_modern"` … enum: `non_modern \| modern_earth`；**modern_earth → 載入器直接回 None（等價於無 pack）**」 | 契約 §1.3，`docs/EH-4-EPISTEMIC-MIND-CONTRACT.md:143-144` |
| 2 | 「**V4**：`civilization_base == "modern_earth"` → 直接 `None`（**不視為錯誤**，是 **D3 bypass 的資料側表達**）」 | 契約 §1.4，`:183` |
| 3 | 「白名單在**讀側（D3）**，`civilization_base: modern_earth` 在**資料側（L1）**。實作階段**禁止**把兩者合併或讓其中一方依賴另一方（避免單點失效導致 7 位現代原生角色被誤掛阻力）」 | 契約 §1.6 註，`:319` |
| 4 | 「無 pack 的 agent（**含全部 7 位現代原生角色**）→ L2 直接回 `None`（**bypass 鋼印**）」 | 契約 §1.3 D-INV-2，`:128` |
| 5 | 「S0 `load_pack(agent_id) is None` → `None`（bypass；**現代原生角色與未建包角色走此路**）」 | 契約 §2.2 判定序，`:484` |
| 6 | 「**Probe 4（現代原生 Bypass 鋼印）**：7 位現代原生角色（`agent_akane`/`agent_mai`/`agent_anna`/`agent_aoi`/`agent_miku`/`agent_ruka`/`agent_yua`）遭遇同一組現代概念」 | 契約 §3，`:913-917` |
| 7 | 「現代原生角色（7 名白名單）→ ❌（無 pack）／**❌（0 差量運算）**」 | 契約 §3 對照表，`:758` |
| 8 | git 脈絡：`epistemic_appraisal.py` 只有 3 個 commit（`958fd51` EH-4.1 建立、`ba8c833` EH-4.2、`0f5eb2c` EH-4.2-FIX-1），**V4 自實作首版即存在**，不是後續遺漏 | `git log -- src/inner_life/epistemic_appraisal.py` |

### 4.3 結論：**刻意的世界觀隔離，不是尚未處理的限制**

1. **V4 是 D3 的「資料側表達」**，語意是「這個角色的**原生常識本來就是現代**」⇒
   沒有「非現代原生物」可用來做比擬 ⇒ 差量機制在語意上無對象。
2. **失敗語意刻意被定義為「沒有差量」而非「錯誤」**（V4「不視為錯誤」、V6 fail-silent）⇒
   這是一種**正確的靜默**，不是缺陷。
3. **雙旁路刻意不互相依賴**（§1.6 註）⇒ 任何單點失效都不會讓現代角色被「誤掛阻力」。
4. **反證（為什麼不能對現代角色放寬）**：對現代原生者，`native_basis` 必然包含電熱器具之類的現代物
   ⇒ **FSA-0 原生免疫閘（詞條 ⊆ 核心名詞）會把幾乎所有現代物判成「原生」**
   ⇒ 差量對撞會變成「似電風扇，卻無電風扇」⇒ **結構上無意義**，不是補資料能救的。
5. **最關鍵的限制**：V4 擋的是**宣稱值**，不是**行為**。
   字面值探測實測：`modern_earth`→拒；`non_modern`/`modern`/`""`/`Modern_Earth`→**全部照樣載入**。
   ⇒ **V4 是一道「誠實性護欄」，不是強制力**：只要有人把一個現代基底角色的 pack 標成
   `non_modern`，它就會被載入並**真的被注入**。⇒ 這正是 `agent_mahiru` 的現況（§9.1）。

---

## 5. 可裁決的選項（≥2，各附代價）

> 針對「現代基底 8 隻要不要有第二來源」這個命題。**本票不動 V4**，以下僅為建議。

### 選項 1：維持現狀，明文化「pack ＝ 非現代基底角色專屬」（**建議**）

- **做法**：V4 不動；把「pack 只服務非現代基底角色」寫進 pack 檔頭與本文件；
  現代基底的第二來源改走**通道 2（關係張力）**與**排程自主觸發**（§5 選項 5）。
- **代價**：現代基底 8 隻的 pack 覆蓋率**恆為 0**；「首次遇到現代物的認知落差」對牠們不存在——
  這在設計上本來就成立（原生常識＝現代常識⇒不該有落差）。
- **收益**：0 新風險；且本票已把「非現代基底」的 2 隻補齊到 **2/2**（原 1/2）。
- **本票已為此鋪好**：新增 `tests/inner_life/test_epistemic_packs.py::test_no_modern_earth_pack_is_shipped`
  ＋ `test_modern_base_agents_have_no_pack` ⇒ **未來有人想替現代角色寫 pack，測試會直接轉紅**。

### 選項 2：放寬 V4，讓 `modern_earth` 的 pack 也能載入

- **做法**：把 V4 從「字面值判斷」改成「僅當該 agent 在 D3 白名單時才作廢」；
  並為現代基底定義一種新 pack 語意（例如「該角色的**已知**生活常識」）。
- **代價（高）**：
  ① 現代角色的 `native_basis` 必然含現代器具 ⇒ **FSA-0 免疫閘全面命中** ⇒ 差量機制仍產不出東西；
     ⇒ 必須**同時**改寫 FSA-0 的免疫池語意（§2.2 判定序）⇒ **動到 frozen 的 V1~V7 與 §2.2 判定序**；
 ② 需要新的契約條文與新的 Probe；
 ③ 風險是「現代角色開始對電器做比擬」——正是 D3 設計要避免的。
- **收益**：表面覆蓋率可從 2/10 拉到 10/10，但**語意上是壞的**（原生對原生比擬）。
- **建議**：**不做**。若 Owner 仍要放寬，最低限度必須**先**解決 ①。

### 選項 3：現代基底的第二來源改走「關係張力／排程自主觸發」，不靠 pack

- **做法**：把現代基底的「主動求知」走 `collect_tensions` 的**通道 2**（`temporal_phenomenology`），
  搭配 `src/inner_life/epistemic_tension_scheduler.py` 的排程觸發（`SOUL_OS_EPISTEMIC_PIGGYBACK` 旗標）。
- **現況支撐**：實測 **10/10 隻 bond 可用**；通道 2 已在生產生效（`agent_rem` 實測 1 條 `relational_tension`）。
- **代價**：解決不了「首次遇到現代物的認知落差」（那是 pack 專屬命題）；且排程觸發點仍缺
  （`logs/SIM-EPIS-ACTIVATE-READY-WORK-ORDER.md` 記錄的戰役 A 殘餘問題 3，本票不碰）。
- **收益**：成本最低、不需要 pack 資料、不動 frozen contract。

### 選項 4：修正 D3 白名單 —— 把 `agent_mahiru` 補進 `MODERN_NATIVE_AGENTS`

- **做法**：`src/memory/sage/horizon.py:61-69` 加入 `agent_mahiru`。
- **為什麼**：她的 persona 世界觀載體是**現代**（公園／傘／粥／神社／聖誕／模特兒／收養），
  但白名單註解把列在「異世界／架空」（`horizon.py:60`）⇒ **白名單與 persona 不一致**。
- **代價**：動 **frozen contract**（`MODERN_NATIVE_AGENTS` 明列 0 改動）⇒ **需 Owner 裁決**。
- **關鍵：今天加是行為中性的**——`agent_mahiru` 現在沒有 pack ⇒ S0 先擋 ⇒ 兩條路徑都是 0 差量。
  加白名單只是**消掉「未來有人標錯 `civilization_base` 就會被注入」的隱性地雷**。

### 選項 5（附錄，本票不做）：把 pack 消費面從「注入」擴到「排程自主觸發」

- `format_epistemic_horizon_delta`（注入路徑）在 `proxy.py:836`／`:1661` **未被旗標閘控** ⇒ 線上生效；
  而 `collect_tensions` 的排程路徑掛在 `SOUL_OS_EPISTEMIC_PIGGYBACK`（**預設 OFF**）⇒ 排程側仍是死的。
- ⇒ **pack 的「第二來源」在注入側是真的，排在行動側還沒點火。** 這是覆蓋率敘事裡必須講清楚的一層。

---

## 6. 對 `roswaal_mansion_v1`（`agent_rem`）的品質擴充建議

> 這是本票唯一「原本就確定有消費者」的 pack。以下 4 條皆為**純資料層**建議，不新增原作事實。

### 建議 1：修 `source_note` 的行號（審計鏈已斷）★ 最高優先

- 現況：`source_note` 寫「`personas/agent_rem.md:34,41,68-69 雙子女僕/家務/泡茶/整理房間`」。
- 實測：persona 於 2026-07-25「CN output pass」重新蒸餾後，行號已位移。
  - 現行 `:34` ＝「昴天塔醒來但失去記憶」（不是家務）
  - 現行 `:41` ＝記憶錨點 A-03（不是家務）
  - 現行 `:68-69` ＝「自我指涉／不要求唯一答案」（不是家務）
  - **真正支撐「家務／泡茶／整理房間」的是 `:26-27`**（「雷姆自願把所有『本來應該是拉姆做的事』攬在自己身上，家務、應對、戰鬥訓練…」「泡茶、整理房間、處理麻煩」）。
- **代價**：0（只改註解文字）；**收益**：恢復可稽核性——`source_note` 是契約指定的審計欄位（§1.3），失效的出處等於沒有出處。

### 建議 2：補 6 個無 bridge 的特徵鍵，避免 AC-3 回退過度泛化 ★ 實測缺陷

- 實測：通用特徵詞表 20 鍵，pack 的 bridge（含 `requires`）只覆蓋 14 鍵；
  **未覆蓋 6 鍵**：`lighting`／`electric`／`self_drive`／`fan_noise`／`automated_chore`／`measure_display`。
- 實測後果（`agent_rem`，取得句型 = 描述新物的主要抽取路徑）：

  | 特徵鍵 | 實測錨點 |
  |---|---|
  | `lighting`（發光） | `似烘爐，卻無人力、手動工序` |
  | `electric`（插電） | `似烘爐，卻無人力、手動工序` |
  | `self_drive`（自己動） | `似烘爐，卻無人力、手動工序` |
  | `fan_noise`（呼呼響） | `似烘爐，卻無人力、手動工序` |
  | `automated_chore`（自動洗） | `似烘爐，卻無人力、手動工序` |
  | `measure_display`（數字） | `似烘爐，卻無人力、手動工序` |

  ⇒ **照明、帶電、自動運轉、風聲、自動家務、數字度量，全部被比擬成「烤爐」**。
  成因有二：① AC-3 退化宣告回退到 `analogy_anchors[0]`（`energy_dynamics` 的第一個是「烘爐」）；
  ② 取得句裡 `acquisition` bridge 位於 pack 順序末端卻被 AC-6 的「回退條款依 pack 出現序」撿到。
- **建議**：為這 6 鍵各補一條 bridge，錨點**只准取自既有 `native_tools`／`analogy_anchors`**
  （例：`lighting → 油燈`／`魔石燈`、`electric → 魔石暖爐`、`self_drive → 風箱`、
  `fan_noise → 風箱`、`automated_chore → 蒸籠`、`measure_display → 日晷`／`時辰`）
  ⇒ **0 新增原作事實**（詞條皆已在 pack 內）。
- **代價**：純資料；**收益**：消除「凡 modernity 皆烤爐」的過度泛化。

### 建議 3：`acquisition` bridge 應最後排序（或加 `requires` 讓它不搶特定特徵）

- 現況：`acquisition` 沒有 `requires` ⇒ 依 AC-6 屬「回退條款」，但它在 pack 順序上排最後，
  卻會在**所有取得句**裡被撈到，壓過物理特徵 bridge。
- **建議**：把 `acquisition` 的 `expect_absent` 收窄（例如加入具體現代熱源詞），
  或對它補 `requires: ["acquisition"]` 之外的顯式條件，讓「有物理特徵時優先走物理 bridge」。
- **代價**：0（資料層）；**收益**：讓取得句的錨點反映**物理特徵**而非「買了東西」這件事本身。

### 建議 4：補一條 `safety_hazard` 的「魔力耗竭」原生項

- 現況：`agent_rem` 的 `safety_hazard.native_basis` 有「火燭走水／煤煙／焦味／毒物／魔獸／瘴氣／詛咒／魔女氣味」，
  但**沒有**「魔力耗竭」——而 `agent_ram` 的 persona 明寫「失去角後連日常生活都變得吃力，
  魔力和體力都遠不如從前」（`personas/agent_ram.md:33`）。雷姆同樣受角／魔力影響。
- **建議**：若 Owner 校準時認為可加，再補「魔力耗竭」；**本票未擅自加入**（屬新增原作事實，需校準）。

---

## 7. 本票的改動與驗證

### 7.1 改動檔案

| 檔案 | 性質 | 說明 |
|---|---|---|
| `configs/packs/roswaal_mansion_ram_v1.yaml` | **新增** | `agent_ram` 的 L1 實例包（`non_modern`）；schema 完全沿用 `roswaal_mansion_v1.yaml` |
| `tests/inner_life/test_epistemic_packs.py` | **新增** | 14 個守衛測試（含 schema／V4／覆蓋率／行為層） |
| `logs/PACK-EXPAND-FINDINGS.md` | **新增** | 本文件 |

**未修改**：`configs/packs/roswaal_mansion_v1.yaml`、`configs/default.yaml`、`src/**`、`personas/**`、`docs/**`、
`tests/test_observability_completeness_1.py`、`data/**`（唯讀）。
**未執行**：`git add` / `git commit` / `git push` / `git stash`。

### 7.2 新 pack 的內容來源（逐條可稽核）

| pack 欄位 | 來源 |
|---|---|
| `agent_id: agent_ram` / `civilization_base: non_modern` | `personas/agent_ram.md:26`（羅茲瓦爾公館首席女僕）、`:27`（鬼族）、`:32-35`（一角出生／魔女教／失去角／魔力／福音書） |
| `energy_dynamics` 的「無須向他人求取即可自持之魔力」 | `personas/agent_ram.md:33`（失去角後須依賴他人的魔力維持生活）——**拉姆專屬**的負向假設 |
| `safety_hazard` 的「角已折而力氣未復」 | `personas/agent_ram.md:33` |
| `labor_domesticity` 的「首席女僕的分派」 | `personas/agent_ram.md:26`、`:100`（公開與工作場合）、`:191`（超出職責之事） |
| `sensory_metrics` 的「一趟巡視的工夫」 | `personas/agent_ram.md:100` |
| 世界載體（公館／爐火／廚房／水井／蒸籠…） | **同一公館**的既有示範基底 `configs/packs/roswaal_mansion_v1.yaml`（不新增原作事實） |
| `source_note` | 逐條列 persona 行號 ＋ 標明「Pilot 示範值待 Owner 校準」（與既有 pack 同一自我聲明） |

**單字長度防護**：`native_basis`／`native_tools` 每一詞條長度 ≥ 2（實測 0 個違規；凡涉「角」一律寫「鬼角」「斷角」）。

### 7.3 實測行為（`agent_ram` 新 pack）

以 `utterance="主人買了一個會發熱的東西"` 實測：

```
appraise → DeltaRecord(anchor='似壁爐，卻無柴火、火、魔石、魔力', axes=('energy_dynamics','labor_domesticity'))
format_epistemic_horizon_delta → 含 '[本體參照]'
collect_tensions(now, last_interaction_ts=真實) → 1 條 kind='epistemic_gap'
```

（`agent_rem` 同一句仍產出 `似烘爐，卻無柴火、火、魔石`；兩隻 pack 的缺席集合差異
＝ 拉姆多一項「魔力」——對應她「依賴他人魔力」的 persona。）

### 7.4 🔴 生產行為影響（主大腦須知）

1. **注入路徑線上生效且不受旗標閘控**：`proxy.py:836`（group）／`:1661`（private）
   → `_format_horizon_block` → `_format_epistemic_delta` → `format_epistemic_horizon_delta`。
   ⇒ 從**服務重啟後**起，與 `agent_ram` 的對話中若出現帶物理特徵詞的現代物描述，
   她的 system prompt 會多出 `[本體參照]` 段（此前沒有）。
2. **🔴 需要服務重啟才會生效**：載入器有**進程內 dict 快取**且**0 熱重載**
   （契約 §1.6：`快取 | 進程內 dict 快取（pack 為靜態資料；0 檔案監看、0 熱重載）`；
   `src/inner_life/epistemic_appraisal.py:250`、`:267-269`）。
   ⇒ 已在執行的服務行程若曾對 `agent_ram` 走過評估（會），其快取裡就是 `None`，
   **新增檔案不會被讀到** ⇒ 必須重啟。
   **重啟需 Owner 明示授權**（AGENTS.md 行程安全鐵律）⇒ **本票未重啟、未動任何行程**。
3. **排程側仍是死的**：`collect_tensions` 的排程觸發掛 `SOUL_OS_EPISTEMIC_PIGGYBACK`（**預設 OFF**）
   ⇒ 「第二來源」的**行動面**尚未點火（本票不碰旗標）。

---

## 8. 測試結果與變異實驗

> **解譯器＝repo 內的 `.venv`（Python 3.11.15）**。PATH 上的全域 pytest 是另一個世界，不可引用其數字。

### 8.1 新測試檔

```
.venv\Scripts\python.exe -m pytest -q tests/inner_life/test_epistemic_packs.py
→ 14 passed in 2.12s
```

### 8.2 變異實驗（7 條，**每條都真的轉紅**）

驅動器：`%TEMP%\pack_measure\pack_expand_mutation.py`（**不在 repo 內** ⇒ 不被 pytest 收集、
也在 R5 行程護欄掃描範圍外；沿用 `logs/SIM-EPIS-PIGGYBACK-1-IMPLEMENTATION.md` §3 的既有慣例）。
流程：**變異前跑守衛節點（須綠）→ 套用變異 → 再跑（須紅）→ 還原**。
所有變異都套用在 **`%TEMP%` 的 pack 副本**（`PACK_TEST_PACK_DIR` 指向它）⇒ **0 觸達生產**；
每條變異前後比對生產 `configs/packs/` 的 sha256。

| # | 變異 | 轉紅的守衛 | 變異前 rc | 變異後 rc | 生產未被改動 |
|---|---|---|---|---|---|
| M1 | 必要欄位改名（`native_basis` → `native_base`） | `test_pack_schema_conformance` | 0 | 1 | ✅ |
| M2 | pack 內容清空成 `{}` | `test_every_pack_file_loads_via_loader` 等 6 條 | 0 | 1 | ✅ |
| M3 | pack 指向不存在的 agent（`agent_nobody`） | `test_non_modern_base_agents_have_a_pack` 等 4 條 | 0 | 1 | ✅ |
| M4 | 移除 pack 檔 | `test_core_roster_pack_coverage_is_exactly_two_of_ten` 等 4 條 | 0 | 1 | ✅ |
| M5 | `civilization_base` 改成 `modern_earth` | `test_no_modern_earth_pack_is_shipped` 等 6 條 | 0 | 1 | ✅ |
| M6 | `bridge.requires` 換成未知特徵鍵 | `test_pack_schema_conformance` | 0 | 1 | ✅ |
| M7 | **量測名單少算一隻 agent**（抽掉 `agent_ram`） | `test_measured_roster_matches_the_configured_core_ten`、`test_core_roster_pack_coverage_is_exactly_two_of_ten` | 0 | 1 | ✅ |

**7/7 條：變異前綠（rc=0）→ 變異後紅（rc=1）→ 生產 `configs/packs/` sha256 逐位元一致。**

> M7 註：本分支**有** pack 產出，故 pack 變異照做；**另外**補上量測守衛的變異
> （工單 §驗收 3 的備援要求），證明「表格少算一隻 agent」時測試確實轉紅。

### 8.3 D-INV-1／S1 世界觀名詞洩漏檢查（**自加的自我檢查**，非既有測試）

既有 S1 測試（`tests/test_epistemic_mind_eh41.py:301`）只掃 `ea.__file__` **且只看 `agent_rem` 的 pack**。
本票另以同一規則掃**全部 `src/**.py`** 與**本票新 pack 的 73 個詞條**：

| pack | 詞條數 | 未豁免洩漏（`src/**.py`） |
|---|---|---|
| `roswaal_mansion_v1`（既有） | 76 | 2：`魔力`（`src/agent/consciousness.py`、`src/llm/proxy.py`）、`詛咒`（`src/llm/proxy.py`）——皆在 **S1 掃描範圍外** |
| `roswaal_mansion_ram_v1`（本票新增） | 73 | 2：**與既有 pack 完全相同**的 `魔力`、`詛咒`（同一世界、同一 canon） |

⇒ **本 pack 未引入任何新的世界觀名詞硬編碼**（與既有 pack 同一洩漏水準）。
（`焦味` 屬通用特徵詞表，已豁免。）
初次撰稿時本檔曾含「家務」「柴火」兩個詞條，實測發現它們是 `src/` 內的既有字串
（`家務` 出自 `src/inner_life/epistemic_appraisal.py:161` 的通用詞條「自動完成家務」；
`柴火` 出自 `src/memory/sage/provider.py:240` 的 docstring 範例）
⇒ **已刪除**（兩者本就與「端茶／備膳／收拾／巡視／公館的爐火」語意重疊），刪除後行為不變、
錨點輸出不變（實測重跑確認）。

### 8.4 全庫回歸

見 **§10.4**（執行結果：`4 failed, 6909 passed, 24 skipped`，826.65s）
與 **§10.1~§10.3**（4 條失敗的逐條定位與處置）。

---

## 9. 超出本票範圍但必須回報的發現

### 9.1 `agent_mahiru`：persona 是現代基底，卻不在 D3 白名單（**待 Owner 裁決**）

- persona 的世界觀載體：**公園／秋千／傘、發燒／粥／打掃房間、聖誕燈／神社／初詣、母親來訪／被收養、模特兒與社交場合**
  （`personas/agent_mahiru.md:295-298`、`:55`）⇒ **現代生活基底**。
- 但 D3 白名單的註解把她列在「異世界／架空」（`src/memory/sage/horizon.py:60`），且白名單本身**不含**她。
- **後果（量測事實）**：她只受 **V4 一道**旁路，**不受讀側 D3 旁路** ⇒
  **只要有人替她寫一份標成 `non_modern` 的 pack，它就會被載入並真的注入**
  （V4 只認宣稱值，§4.3 第 5 點）。
- **本票的處置**：**不寫她的 pack** —— persona 的日常載體是現代，
  要寫就得捏造異世界日常（違「不得新增原作事實」）；而誠實標 `modern_earth` 會被 V4 整包作廢（無人會讀）。
  ⇒ 改以**選項 4**（補白名單，行為中性）上呈 Owner。

### 9.2 本票範圍外的 4 隻**明確**異世界候選（下一張票的優先名單）

persona 內含**明文**「異世界條款（あり職組共用 · 不可違反）」的角色：

| agent_id | persona 出處 | 判讀 |
|---|---|---|
| `agent_kaori` | `personas/agent_kaori.md`（「異世界條款（あり職組共用 · 不可違反）」段） | 異世界（あり職組） |
| `agent_shea` | `personas/agent_shea.md`（同上段落） | 異世界（あり職組） |
| `agent_shizuku` | `personas/agent_shizuku.md`（同上段落；另有「劍術訓練」記憶錨點） | 異世界（あり職組） |
| `agent_yue` | `personas/agent_yue.md`（同上段落；另有「三百年的凍結」「雪原迷宮」） | 異世界（あり職組） |

> ⚠️ 這 4 份 persona **正被另一條平行工作線正規化**（`scripts/normalize_persona_format.py`：
> 把 `•` 串接的行拆成獨立條列）⇒ **行號會位移**。故此處以**引文**為準，不以行號為準。
> （本票的核心 10 隻 persona **未被**該工作線觸碰，故 §2 表內與 §7.2 的行號引用仍有效。）

⇒ 這 4 隻**不在 D3 白名單**、persona 明文異世界 ⇒ **V4 不會擋** ⇒ 屬於「pack 可用但尚無 pack」的角色。
**本票未為它們寫 pack**（超出「10 隻核心 agent」範圍，且各自需要 persona 校準）⇒ 建議另開一票。
（另有 `agent_asuna`（SAO）、`agent_sakura`（TYPE-MOON，persona 自述「她的機械是現代人程度」）屬**混合基底**，
本票不臆斷，列為下一票的判定項。）

### 9.3 覆蓋率的分母口徑

本票分母＝核心 10 隻（`configs/default.yaml` 前 10 條）。
若以全庫 **34** 隻註冊角色為分母，覆蓋率為 **2/34 = 5.9%**，且其中 4 隻明文異世界候選尚未計入。

---

## 10. 驗收對帳

| 工單驗收項 | 狀態 | 證據 |
|---|---|---|
| ① `logs/PACK-EXPAND-FINDINGS.md` 含 10 隻逐隻表（每個數字寫明分子／分母／單位與來源） | ✅ | §2、§2.1 |
| ② 分支判定與依據 | ✅ | §3（並修正「9 隻」→「8 隻」與「pack 全無用」兩個前提） |
| ③ 分支 A 產出：V4 意圖分析 ＋ ≥2 可裁決選項（附代價）＋ `roswaal_mansion_v1` 品質擴充建議 | ✅ | §4（8 條契約出處）、§5（**5 個選項**，各附代價）、§6（**4 條**建議） |
| ④ `.venv\Scripts\python.exe -m pytest -q tests` **0 failed** | ⚠️ **4 failed（全部已定位、全部預期、全部需主大腦 3 處一行級處置）** | 見 §10.1、§10.2、§10.3、§10.4 |
| ⑤ 變異實驗逐條轉紅 | ✅ **7/7** | §8.2 |
| ⑥ 一句話回答覆蓋率 X → Y | ✅ **1/10 → 2/10** | §0 |

### 10.1 預期失敗 ①②：`tests/test_epistemic_mind_eh41.py` 守著「拉姆還沒建包」的舊前提

```
FAILED tests/test_epistemic_mind_eh41.py::test_e_other_non_modern_agent_without_pack_also_bypasses
FAILED tests/test_epistemic_mind_eh42.py::TestD_Regression::test_d_legacy_suites_green
```

- **成因**：該守衛的原文 docstring 就寫著「**第二個異界角色尚未建包**」：

  ```python
  # tests/test_epistemic_mind_eh41.py:271-275
  def test_e_other_non_modern_agent_without_pack_also_bypasses():
      """無 pack 的非現代角色（第二個異界角色尚未建包）→ bypass，不誤掛阻力。"""
      assert load_epistemic_pack("agent_ram") is None
      assert format_epistemic_horizon_delta("agent_ram", "我用氣炸鍋弄了豆腐，它一直吹熱風") == ""
      assert format_epistemic_horizon_delta("", "我用鐵鍋炒了菜") == ""
  ```

  ⇒ 本票替 `agent_ram` 建了 pack，該守衛的**前提被本票正面推翻**（這是預期後果，不是回歸）。
  `eh42` 的失敗是**連鎖**：它只是用 subprocess 重跑 `eh41`（`tests/test_epistemic_mind_eh42.py:320-333`）
  ⇒ **改 1 處即可同時修好 2 條失敗**。

- **建議的一行級修法（已實測可用；本票未動他人檔案）**
  把守衛的**代表角色**從「已建包的 `agent_ram`」換成「同樣是異世界、但仍未建包的 `agent_yue`」：

  ```python
  # tests/test_epistemic_mind_eh41.py:271-275
  def test_e_other_non_modern_agent_without_pack_also_bypasses():
      """無 pack 的非現代角色（異世界但尚未建包）→ bypass，不誤掛阻力。

      （PACK-EXPAND：agent_ram 已建包 configs/packs/roswaal_mansion_ram_v1.yaml，
        故代表角色改為同為異世界、仍未建包的 agent_yue。）
      """
      assert load_epistemic_pack("agent_yue") is None
      assert format_epistemic_horizon_delta("agent_yue", "我用氣炸鍋弄了豆腐，它一直吹熱風") == ""
      assert format_epistemic_horizon_delta("", "我用鐵鍋炒了菜") == ""
  ```

  **已實測**（本機，非預測）：`agent_yue` → `load_epistemic_pack` = `None`、
  `format_epistemic_horizon_delta("agent_yue", "我用氣炸鍋弄了豆腐，它一直吹熱風")` = `""`、
  `format_epistemic_horizon_delta("", "我用鐵鍋炒了菜")` = `""` ⇒ 三條斷言全成立。
  （該斷言只依賴「無 pack ＋ 不在 D3 白名單」，**不依賴 persona 內容**
  ⇒ 不受那條平行工作線的 persona 正規化影響。）
  備選代表角色（同樣實測通過）：`agent_kaori`／`agent_shea`／`agent_shizuku`／`agent_mahiru`。

- **為何本票不自己改**：工單明列可寫入檔案只有 3 個
  （`configs/packs/*.yaml`、`tests/inner_life/test_epistemic_packs.py`、`logs/PACK-EXPAND-FINDINGS.md`），
  且對他人檔案採「**在報告寫出需要加哪一筆，不要自己改**」的處置。

### 10.2 預期失敗 ③：`configs/**` 的非 `.py` 檔數量被釘死 ⇒ 新增 pack 檔必須 bump 釘死值

```
FAILED tests/soul/test_life_thread_wake_gate_m3.py::test_t7_non_python_dirs_still_zero_reference[configs]
AssertionError: configs 被掃描的非 .py 檔數變了（3 != 2）⇒ 覆蓋面已漂移，請確認新檔案是否引用 M3：
['configs/default.yaml', 'configs/packs/roswaal_mansion_ram_v1.yaml', 'configs/packs/roswaal_mansion_v1.yaml']
```

- **成因（純計數，不是行為回歸）**：該護欄把 `configs/**` 的非 `.py` 檔數**寫死**
  （`tests/soul/test_life_thread_wake_gate_m3.py:790-793`），本票新增 1 個 pack yaml ⇒ 2 → 3。
  護欄註解本身就寫明這個設計意圖：「**新增一個檔案就會紅，逼人回來確認它是否引用了 M3**」。
- **該守衛要求的那個確認，本票已完成（實測，非預測）**：
  - `configs/**` 被掃描的非 `.py` 檔＝3 個，**`life_thread_wake_gate` 命中數 = 0**
    ⇒ **新 pack 未引用 M3** ⇒ bump 計數是安全的。
  - （該護欄的 `hits == []` 斷言位於計數斷言之後，故本次只停在計數行；
    上面的獨立掃描已把 `hits` 單獨驗證為空。）
- **建議的一行級修法（本票未動他人檔案）**：

  ```python
  # tests/soul/test_life_thread_wake_gate_m3.py:790-793
  _M3_NON_PY_SCANNED_COUNTS = {
      "configs": 3,   # ← 2 改 3：新增 configs/packs/roswaal_mansion_ram_v1.yaml
      "clients": 4,
  }
  ```

### 10.3 預期失敗 ④：hygiene 守衛要求新測試檔必須在 git index 裡

```
FAILED tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked
```

- **成因**：該守衛要求 `tests/**` 下每個可收集的測試檔都必須在 **git index** 裡
  （`tests/infra/test_repo_hygiene_tracked_tests.py:281-293`）。本票新增的
  `tests/inner_life/test_epistemic_packs.py` 尚未被 `git add`。
- **本票為何不做**：工單明令「**不得 `git add` / `git commit` / `git push` / `git stash`**」
  （提交由主大腦統一處理）。⇒ **只回報，不動手。**
- **主大腦的一行處置**：
  ```
  git add tests/inner_life/test_epistemic_packs.py
  ```
  （`git add` ≠ commit；repo 自身的先例見 `logs/SIM-EPIS-ACTIVATE-READY-IMPLEMENTATION.md:6-8`。）
  ⚠️ 平行工作線另有未追蹤的 `tests/persona/`（他人檔案）⇒ 同一個守衛可能也會把它算進去，
  請一併 `git add`（**該檔不屬本票**）。

### 10.4 全庫回歸（repo 內 `.venv`，Python 3.11.15）

```
.venv\Scripts\python.exe -m pytest -q tests
→ 4 failed, 6909 passed, 24 skipped, 2 warnings, 27 subtests passed in 826.65s (0:13:46)
```

| # | 失敗 | 歸屬 | 處置 |
|---|---|---|---|
| 1 | `tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked` | 本票（新測試檔未入 index）＋平行工作線的 `tests/persona/` | `git add`（§10.3） |
| 2 | `tests/soul/test_life_thread_wake_gate_m3.py::test_t7_non_python_dirs_still_zero_reference[configs]` | **本票**（`configs/**` 非 `.py` 檔數 2→3） | bump 釘死值（§10.2） |
| 3 | `tests/test_epistemic_mind_eh41.py::test_e_other_non_modern_agent_without_pack_also_bypasses` | **本票**（守衛的「拉姆尚未建包」前提被本票正面推翻） | 換代表角色（§10.1） |
| 4 | `tests/test_epistemic_mind_eh42.py::TestD_Regression::test_d_legacy_suites_green` | **連鎖**（用 subprocess 重跑 `eh41`） | 同 #3，一處修改同時修好 |

**除這 4 條外，全庫 0 failed。**
（另註：本機 `pytest` 過程中另有 2 個 `PytestUnhandledThreadExceptionWarning`
—— `tests/test_m1_5_night_400_fix.py` 以 `text=True` 讀 subprocess 輸出時撞上 Windows 的
`cp950` 解碼（`UnicodeDecodeError: 'cp950' codec can't decode byte 0xe7`）⇒ **與本票無關**、
且為 warning 非 failure。）

#### 10.4.1 ⚠️ 基準線在移動：平行工作線造成的第 5 條失敗（**不是本票**）

在本票量測期間，另一條平行工作線（persona 正規化）動了 `src/**`：

```
FAILED tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched
AssertionError: src/ 改動超出許可集: {'src/memory/sage/provider.py'}
```

- **歸屬**：**平行工作線，不是本票**。判定依據（本機實測）：
  ① 本票的 3 個檔案**全部是未追蹤的新檔**（`git status --short` 顯示 `??`）
  ⇒ **不會出現在 `git diff --name-only HEAD`** ⇒ 該守衛（以 `git diff HEAD` 為基準）
  **結構上不可能**因本票而失敗；
  ② `src/memory/sage/provider.py` 的改動來自平行工作線（`git diff --stat` 顯示 +5/-1），
  本票全程 **0 修改 `src/**`**。
- **本票的 `configs/default.yaml` 狀態**：本票**未修改**它；但平行工作線已把註冊角色
  從 34 增加到 **46** ⇒ 本票的量測守衛（`configs/default.yaml` 的**前 10 條**＝核心 10 隻）
  **仍然成立**（實測：前 10 條未變、`14 passed`）。
- ⇒ **主大腦複核時請注意**：全庫回歸的基準線會隨平行工作線變動；
  本票的 4 條失敗與平行線的 1 條失敗**必須分開歸因**。

### 10.5 主大腦的三處一行級處置（彙總）

```bash
# ① 新測試檔入 index（hygiene 守衛）
git add tests/inner_life/test_epistemic_packs.py

# ② tests/soul/test_life_thread_wake_gate_m3.py:791 → 2 改 3
#    _M3_NON_PY_SCANNED_COUNTS = {"configs": 3, "clients": 4}

# ③ tests/test_epistemic_mind_eh41.py:271-275 → 代表角色 agent_ram 改 agent_yue
#    （三條斷言已實測在 agent_yue 上成立；docstring 同步更新）
```

### 10.6 排除本票 4 條後的全庫複核（證明「除那 4 條外全綠」）

```powershell
.venv\Scripts\python.exe -m pytest -q tests `
  --deselect "tests/infra/test_repo_hygiene_tracked_tests.py::test_every_collectable_test_file_under_tests_is_git_tracked" `
  --deselect "tests/soul/test_life_thread_wake_gate_m3.py::test_t7_non_python_dirs_still_zero_reference[configs]" `
  --deselect "tests/test_epistemic_mind_eh41.py::test_e_other_non_modern_agent_without_pack_also_bypasses" `
  --deselect "tests/test_epistemic_mind_eh42.py::TestD_Regression::test_d_legacy_suites_green"
→ 2 failed, 7055 passed, 24 skipped, 4 deselected, 1 warning, 27 subtests passed in 830.93s (0:13:50)
```

剩餘的 **2 條失敗全部來自平行工作線**（與本票無關，見 §10.4.1）：

| 失敗 | 歸屬 | 原因 |
|---|---|---|
| `tests/agent/test_agent_registry_land0_batch1.py::test_guard3_persona_files_are_byte_identical_to_drafts` | 平行工作線 | persona 正在被正規化（`scripts/normalize_persona_format.py`）⇒ 不再與 drafts 位元相同 |
| `tests/test_observability_completeness_1.py::TestBoundedAndFrozenDiscipline::test_no_frozen_contract_file_touched` | 平行工作線 | `src/memory/sage/provider.py` 被改動，不在該票的 allowed 清單內 |

⇒ **本票的 4 條失敗就是本票的全部失敗**；把這 4 條依 §10.5 處理掉之後，
本票對全庫回歸的影響為 **0**（其餘 7055 筆全綠）。

---

## 11. 重現步驟

```powershell
# 1) 量測（唯讀；產出本文件的表）
& .venv\Scripts\python.exe $env:TEMP\pack_measure\measure.py

# 2) 新 pack 守衛
& .venv\Scripts\python.exe -m pytest -q tests/inner_life/test_epistemic_packs.py

# 3) 變異實驗（7 條；0 觸達生產）
& .venv\Scripts\python.exe $env:TEMP\pack_measure\pack_expand_mutation.py

# 4) 全庫回歸（需先 git add 新測試檔，否則 hygiene 守衛會紅）
& .venv\Scripts\python.exe -m pytest -q tests
```

---

## 12. Frozen Contract 自我檢查

| Frozen 項 | 本票是否觸碰 | 證據 |
|---|---|---|
| `src/inner_life/` Agency 4 stages／TriggerEnvelope／InnerLifeEvent／4 handlers／SAGE 寫入邏輯 | ❌ 未觸碰 | **本票**的改動僅 3 個新檔（全部未追蹤）；`src/**` 內的現存改動來自平行工作線，非本票 |
| `epistemic_appraisal` 的 V1~V7 規則語意 | ❌ 未觸碰（只分析、只建議） | `src/inner_life/epistemic_appraisal.py` 0 改動 |
| `LONGING_THRESHOLD` 與 G4 | ❌ 未觸碰 | `src/soul/scheduler.py` 0 改動 |
| `MODERN_NATIVE_AGENTS` 白名單 | ❌ 未觸碰 | `src/memory/sage/horizon.py` 0 改動（僅回報建議 4） |
| `data/**` 生產狀態 | ❌ 未寫入／未改名／未刪除 | 全程唯讀；sqlite 先複製到 `%TEMP%` |
| 行程安全（不殺行程、不啟停服務、不重啟） | ❌ 未觸碰 | 0 個行程指令；新 pack **需重啟才生效**（§7.4-2），重啟待 Owner 授權 |
