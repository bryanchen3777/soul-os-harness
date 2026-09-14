# ANCHOR-A-SUPPLY-INVESTIGATION — 錨 A（活動由來）供給鏈調查（READ-ONLY，實作前置）

| 項目 | 值 |
|------|-----|
| 工單 | **ANCHOR-A-SUPPLY-1** |
| 性質 | **純 READ-ONLY 調查** —— **0 實作、0 測試、0 服務重啟、0 `data/**` 寫入** |
| 階段 | 階段 0 白天備料期（Owner 明確授權此範圍） |
| 起始 HEAD | `da50452`（docs: sync Current HEAD to 566a444） |
| 前置文件 | `docs/TRANSMIT-GROUNDING-SPEC.md`（`566a444`）——**本票只補它標為前置/未追查的缺口（§11.3-4），不重做它已量測的部分** |
| 交付物 | `docs/ANCHOR-A-SUPPLY-INVESTIGATION.md`（本檔，唯一新檔） |
| 紅線遵守 | 0 `src/**` / 0 `scripts/**` / 0 `tests/**` / 0 `configs/**` / 0 `data/**` 變更、0 服務重啟、未開 `data/faulthandler.log`、未用 `git add -A` |

> **本檔每一個數字都附來源（檔名／行號／樣本量）。量不到的一律寫 NO DATA 並說明缺什麼觀測（§4）。**
> **本檔不把「建議」寫成「現況」——建議一律置於 §3.4／§3.5 並標「（候選方案）」。**

---

## §0 方法與量測聲明（先讀）

### 0.1 量測邊界（與 SPEC §0.1 同級）

在**生產 24 小時穩定性觀察期**內執行，因此全程只讀：

- `data/**` 僅以 `Get-Content` / `Select-String` / Python `sqlite3`（`mode=ro`）讀取。**0 寫入、0 改名、0 截斷**（前後對照見 §6.1）。
- **`data/faulthandler.log` 未被開啟、未被觸碰**（前後 size/mtime 一致，見 §6.1）。
- **未執行任何測試**（含任何會寫入 `data/**` 者）。
- 未執行任何 `server_ops` / watchdog / restart 動作（四埠 PID 前後一致，見 §6.2）。

### 0.2 一項**必須記載的方法論陷阱**（否則本票所有數字都會錯）

本機 `pwsh` 為 **PowerShell 5.1**（`5.1.26100.9444`）。`Get-Content` 預設編碼是 ANSI，而 diary jsonl 是 **UTF-8 無 BOM**：

```powershell
# 預設編碼 → mojibake 讓 JSON 解析失敗
foreach($f in $files){ foreach($l in (Get-Content $f.FullName)){ $null = $l|ConvertFrom-Json } }
#   → ok=148 / bad=950   ❌ 只有 13% 的行解析成功

# 正確：明確指定 UTF8
foreach($f in $files){ foreach($l in (Get-Content $f.FullName -Encoding UTF8)){ $null = $l|ConvertFrom-Json } }
#   → ok=1098 / bad=0    ✅ 100%
```

> ⚠️ **任何後續以「逐行 JSON 解析」量測 diary 的工單（含實作票的驗收腳本）都必須指定 `-Encoding UTF8`，否則會得到約 13% 的假樣本。** 本票所有 funnel 數字皆以 `-Encoding UTF8` 取得。
> （SPEC §0.2 用 `Select-String` 抓 ASCII 子字串，不受編碼影響，故其 9/7 兩個數字仍然正確——見 §0.4 交叉驗證。）

### 0.3 樣本與窗口定義（本檔全篇適用）

| 名稱 | 定義 | diary 檔數 | entry 行數 | **agent-day 數**<br>（＝有 diary 檔的 (agent,date) 配對） |
|------|------|-----------|-----------|------------------------|
| **WHOLE** | 全部 diary 檔（`2026-07-19` ～ `2026-09-13`，57 個日期） | 442 | **1098** | **442** |
| **7D** | 檔名日期 `2026-09-07` ～ `2026-09-13` | 70 | **149** | **70** |
| **TODAY** | 檔名日期 `2026-09-13` | 10 | **12** | **10** |
| **POST-M7-1** | 檔名日期 `>= 2026-08-18`（M7-1 活動模型化落地日，`dream_event.py:215`） | 235 | **547** | **235** |

- 有 diary 的 agent 目錄 11 個（10 隻在線角色 ＋ `agent_newcomer`）；實際生產角色 10 隻。
- **`agent-day` 是本檔的產率分母**（例：`7 筆 / 70 agent-day = 0.1 筆/角色/日`）。SPEC §1.4b 的「≈0.1 筆/角色/日」即此口徑。
- 單次量測時刻：**2026-09-13T12:52（本地）**。

### 0.4 與 SPEC 的交叉驗證（確認本票量測與 SPEC 對齊）

| 指標 | SPEC（`566a444`，mtime 口徑） | 本票（檔名日期口徑） | 一致？ |
|------|------------------------------|---------------------|--------|
| 7 日 `slot=event` 行數 | 9 | **9** | ✅ |
| 7 日 `shareable:true` 行數 | 7 | **7** | ✅ |
| 今日 `shareable:true` | 0/10 | **0/10** | ✅ |
| 今日 `slot=event` | 2 | **2** | ✅ |

→ **兩種獨立量測口徑完全一致，本票的擴充量測建立在已交叉驗證的基礎上。**

---

## §1 (a) `shareable` 的寫入者、語意與逐項過濾判定邏輯

### 1.1 🔴 寫入者：全 repo **只有一處**生產寫入（path:line）

```grep
grep -rn "shareable" --include=*.py src/ scripts/
```

| # | 位置 | 角色 | 內容（逐字） |
|---|------|------|-------------|
| **W1** | **`src/soul/dream_event.py:499`** | **唯一寫入** | `entry["shareable"] = activity.get("shareable", False)` |
| W1-上游 | `src/soul/dream_event.py:496-499` | 寫入條件 | `if activity:` → 三欄一起寫（`activity` :497 / `category` :498 / `shareable` :499） |
| V1 | `src/soul/dream_event.py:220-231` | **值來源（靜態常數池）** | `ACTIVITY_POOL`，**10 筆固定項**：6 筆 `shareable: True`（工作/做飯/吃東西/運動/創作/散步）＋ 4 筆 `shareable: False`（看書/整理房間/聽音樂/發呆） |
| C1 | `src/soul/dream_event.py:669` | 呼叫端 1 | `activity = random.choice(ACTIVITY_POOL)`（`write_event`） |
| C2 | `src/soul/scheduler.py:1008` | 呼叫端 2 | `activity = random.choice(ACTIVITY_POOL)`（`_fire_shared_event`，跨角色共用活動） |
| **R1** | **`src/soul/scheduler.py:1226`** | **唯一判定（讀取）** | `if entry.get("shareable") is not True: continue` |

- **除上述之外，`src/**` 內 0 處寫入、0 處讀取 `shareable`**。其餘命中的 99 處全部落在 `tests/**`（測試樣本）、`docs/**`（歷史計畫書）、`logs/ENGINEERING_STATE.md`（登記文字）。
- **`shareable` 未參與任何 schema 定義、未參與任何 store、未參與任何 SI-2.1 元件。**

### 1.2 🔴 語意判定（本調查的關鍵結論之一）

| 問題 | 判定 | 證據 |
|------|------|------|
| 是「可分享性／隱私等級」？ | ❌ **不是隱私等級** | 值來自**靜態常數池的逐項字面值**，與任何隱私輸入（`is_private` / `channel` / `source_mode` / `visibility`）**無資料流關係**。`_write_entry`（`dream_event.py:446-512`）簽名中根本沒有隱私參數。 |
| 是「活動是否已完成／可用」？ | ❌ 不是 | 與 `slot`／`state`／任何完成度欄位無關。 |
| 那是什麼？ | ✅ **「這類活動值不值得跟 Bry 分享」——活動類型的美學／社交屬性** | 寫入端註解逐字：`dream_event.py:219`「`shareable`: 是否值得跟 Bry 分享 (供 M7-2 活動驅動主動傳訊判斷用)」；`docs/INNER-LIFE-AND-PROACTIVE-PLAN.md:88`「值得跟 Bry 分享的標記」 |
| 判定粒度 | **粗粒度（10 選 1 的查表），非逐筆判定** | 同一活動名（如「散步」）在任何角色、任何日期都得到同一個 `shareable` 值 |

**→ 直接推論：放寬 `shareable` 不會讓渡任何「隱私判定」，只讓渡「值不值得分享」這一層語意選擇。**（這正好回答 SPEC OQ-1 的關鍵問題，見 §1.6 的邊界說明。）

### 1.3 🔴 關鍵側發現：`shareable:false` 有**兩種完全不同的成因**（欄位缺失 ≠ false）

`_write_entry` 只在 `activity` 非空時才寫 `shareable`（`dream_event.py:496`）。三條呼叫分支的行為不一致：

| 呼叫分支 | `source` | 是否傳 `activity` | entry 是否含 `shareable` | 程式位置 |
|---------|---------|------------------|------------------------|---------|
| `write_event` LLM 成功／超長截斷 | `llm` | ✅ 傳 | ✅ 含（值＝池中值） | `dream_event.py:713-723` |
| **`write_event` placeholder（LLM 失敗／think_only）** | `placeholder` | ❌ **不傳** | ❌ **欄位整個不存在** | `dream_event.py:695-706` |
| `write_shared_event` LLM 成功 | `llm` | ✅ 傳 | ✅ 含 | `dream_event.py:803-805` |
| **`write_shared_event` placeholder** | `placeholder` | ✅ **傳** | ✅ **含**（可能是 `True`） | `dream_event.py:785-797` |

**後果**：`shareable:false` 在下游有兩種成因，而 SPEC §1.4c 的結論「欄位齊備，唯一卡點是 `shareable: false`」**只對今日那 2 筆成立，不能推廣到全體**：

1. **成因 ①：明確被判 `False`** —— LLM 成功但抽到池中 4 筆 `shareable:False` 的活動（今日 2 筆即此類）。
2. **成因 ②：欄位從未寫入**（`ABSENT`）—— `write_event` 的 placeholder 路徑；下游 `entry.get("shareable") is not True` 對 `None` 同樣擋下。

> **判定**：這**不是**「被判定為不可分享」，也不是單一 bug；是「**粗粒度查表**（成因①）＋**欄位缺寫**（成因②）」兩件事併存。成因②在近 7 日窗口為 **0 筆**（見 §1.5），故**近期供給的卡點是成因①，不是欄位缺失**。

### 1.4 漏斗表（三窗口，逐條件量測）

條件順序完全依 `src/soul/scheduler.py:1224-1231`（**注意：程式實為 5 道檢查，SPEC 口徑算 4 道；第 5 道 = `activity` 非空，量測顯示現階段恆為 0 篩除，見下**）：

| 階段 | 條件（`scheduler.py` 行號） | **WHOLE** | 佔前一階段 | **7D** | 佔前一階段 | **TODAY** |
|------|---------------------------|----------:|-----------:|------:|-----------:|----------:|
| N0 | 全部 diary entry 行數 | **1098** | — | **149** | — | **12** |
| N1 | `entry.get("slot") != "event"` → continue（:1224） | **100** | **9.11%**（篩掉 998／90.89%） | **9** | **6.04%**（篩掉 140／93.96%） | **2** |
| N2 | `entry.get("shareable") is not True` → continue（:1226） | **17** | **17.00%**（篩掉 83／83.00%） | **7** | **77.78%**（篩掉 2／22.22%） | **0** |
| N3 | `entry.get("source") != "llm"` → continue（:1228） | **9** | **52.94%**（篩掉 8／47.06%） | **7** | **100.00%**（篩掉 0） | **0** |
| N3a | `not entry.get("activity")` → continue（:1230，第 5 道） | **9** | **100.00%**（篩掉 0） | **7** | **100.00%**（篩掉 0） | **0** |
| **N4** | ＋**必須在「今日檔」**（:1210-1211 `f"{today}.jsonl"`） | **0** | **0.00%**（篩掉 9／100%） | **0** | **0.00%**（篩掉 7／100%） | **0** |
| 合計 | N0 → N4 總通過率 | **0 / 1098 = 0.00%** | | **0 / 149 = 0.00%** | | **0 / 12** |

### 1.5 🔴 阻擋大戶判定

**沒有一條條件單獨構成全部障礙，但有明確的主從：**

| 排名 | 條件 | 篩除量（WHOLE） | 篩除量（7D） | 性質 |
|------|------|----------------|-------------|------|
| **① 主因（結構性）** | **`slot=="event"`**（`scheduler.py:1224`） | **998 行／90.89%** | **140 行／93.96%** | **供給源本身產率太低**：event 產率實測 **0.0638 筆/角色/日**（POST-M7-1 `event/llm`，15 筆 / 235 agent-day；`event/placeholder` 另 10 筆 / 0.0426） |
| **② 決定性（時效）** | **「今日檔」**（`scheduler.py:1210`） | 9 → **0** | 7 → **0** | **把 7 日累積的殘量，在「需要它的那一刻」歸零**：7 日內只有 3 天（09-07、09-09、09-10）出現過合格 entry → 7 日中有 **4 日整天的今日檔為 0** |
| **③ 直接卡點（今日）** | `shareable is True`（`scheduler.py:1226`） | 83 行／83.00% | **2 行／22.22%** | **今日的直接卡點**：今日 2 筆 event 全部抽到池中 `shareable:False` 的「發呆」（見 §1.6） |
| ④ 次要 | `source=="llm"`（`:1228`） | 8 行／47.06%（**全部集中在 08-22～08-24**） | 0 | 只在 `write_shared_event` placeholder 期間咬人 |

**🔴 結論（阻擋大戶）**：
**主因是 `slot=="event"`**（結構性供給不足，篩掉 >90%），**而 `shareable` 只是「今日的直接卡點」**——它在近期窗口只篩掉 22%，**單獨放寬它無法補上缺口**（見 §3.3 的定量證明：放寬後 ruka 仍只有 0.167 筆/角色/日，仍差需求 12～30 倍）。**「今日檔」是最後一道致命閘門**：它使任何「按日累積、隔日失效」的設計在低產率下必然歸零。

### 1.6 `shareable:false` 的成因細分（在 `slot=event` 的行內）

**（a）欄位值分布**（`slot=="event"`，n=100）：

| `shareable` 值態 | 行數 | 佔比 |
|-----------------|-----:|-----:|
| **`ABSENT`（欄位不存在）** | **77** | **77.0%** |
| **`TRUE`** | **17** | **17.0%** |
| **`FALSE`（明確 false）** | **6** | **6.0%** |
| `null` / 字串 / 其他型別 | **0** | 0% |

**（b）× `source` 交叉表**：

| 窗口 | 值態 × source | 行數 | 說明 |
|------|--------------|-----:|------|
| WHOLE | `ABSENT` × `llm` | **67** | **全部是 M7-1（`2026-08-18`）之前的歷史行**——當時 `write_event` 尚未帶 `activity` metadata |
| WHOLE | `ABSENT` × `placeholder` | 10 | `write_event` placeholder 路徑（§1.3 成因②） |
| WHOLE | `TRUE` × `llm` | 9 | 合格供給（4 條件全過） |
| WHOLE | `TRUE` × `placeholder` | 8 | **`write_shared_event` placeholder**（`dream_event.py:785-797`）——`shareable` 有值但 `source != llm`，被第 3 道擋下 |
| WHOLE | `FALSE` × `llm` | 6 | 成因①（池中 4 筆非分享類） |
| **7D** | **`TRUE` × `llm`** | **7** | **合格供給** |
| **7D** | **`FALSE` × `llm`** | **2** | **成因①（今日那 2 筆）** |
| 7D | `ABSENT` / `placeholder` 任何組合 | **0** | ← 近期窗口已無成因② |

**（c）POST-M7-1 窗口（`>= 2026-08-18`）逐筆清單（25 筆 event，`activity`/`category`/`shareable`/`source`）**：

```
08-22 miku   placeholder 創作  creative  True
08-22 ruka   placeholder  (無)  (無)      ABSENT   ← write_event placeholder（成因②）
08-22 yua    placeholder 創作  creative  True
08-23 anna   placeholder 運動  sport     True
08-23 mahiru placeholder 運動  sport     True
08-23 miku   placeholder 創作  creative  True
08-23 ruka   placeholder 創作  creative  True
08-24 mahiru placeholder 散步  leisure   True
08-24 mai    placeholder 散步  leisure   True
08-24 ruka   placeholder  (無)  (無)      ABSENT   ← write_event placeholder（成因②）
08-30 ruka   llm         聽音樂 leisure   False   ← 成因①
08-31 anna   llm         運動  sport     True    ✅
08-31 miku   llm         聽音樂 leisure   False   ← 成因①
08-31 rem    llm         運動  sport     True    ✅
08-31 ruka   llm         聽音樂 leisure   False   ← 成因①
08-31 ruka   llm         聽音樂 leisure   False   ← 成因①
09-07 ram    llm         散步  leisure   True    ✅
09-07 rem    llm         散步  leisure   True    ✅
09-07 ruka   llm         散步  leisure   True    ✅
09-09 mai    llm         散步  leisure   True    ✅
09-09 rem    llm         散步  leisure   True    ✅
09-10 anna   llm         創作  creative  True    ✅
09-10 ram    llm         創作  creative  True    ✅
09-13 akane  llm         發呆  rest      False   ← 成因①（今日）
09-13 rem    llm         發呆  rest      False   ← 成因①（今日）
```

**（d）成因判定（逐項回答工單問題）**：

| 問題 | 判定 | 依據 |
|------|------|------|
| 是「從未寫入」還是「被判定為不可分享」？ | **兩者都有，但時序分明**：<br>・M7-1 前（`< 08-18`）：**從未寫入**（欄位不存在）<br>・M7-1 後（`>= 08-18`）：**只有 2 筆**「從未寫入」（皆 `write_event` placeholder），**其餘 23 筆都有寫入**，其中 6 筆（含今日 2 筆）是**查表判定為 false** | 交叉表 (b) ＋ 清單 (c) |
| 今日 `shareable:true = 0/10` 是寫入端行為，還是正常波動？ | **判定為「正常波動 ＋ 系統性偏差」**：<br>・**波動**：池中 `True` 佔 6/10，今日 2 筆連續抽到 `False` 的機率 = 0.4² = **16%**（不罕見）<br>・**系統性偏差**：近期 `True/False` = 9/14 ≈ 64%（接近池的 60%），**無證據顯示寫入端被停用或判定邏輯改變**<br>・**但真正的系統性問題在別處**：event 總產率僅 0.0638／角色／日（`event/llm`），使「同一天有合格 event」本身成為稀有事件 | POST-M7-1 清單 (c)、§1.5 |
| 放寬它安全嗎？ | **技術上安全（無隱私判定會被穿透），但語意上有代價** | §1.2、§1.6(e) |

**（e）今日（`2026-09-13`）2 筆 event 的真實欄位值（唯讀，僅列 metadata 不列 content）**：

```
agent_akane  ts=2026-09-13T12:09:42.637692+00:00  slot=event  source=llm
             activity=發呆  category=rest  shareable=False   (content_len=26)
agent_rem    ts=2026-09-13T12:09:44.379930+00:00  slot=event  source=llm
             activity=發呆  category=rest  shareable=False   (content_len=33)
```

> 註：SPEC §1.4c 引用的同一行顯示 `activity:"???"`，經唯讀核對為**顯示編碼問題**（見 §0.2），實際值為 **`發呆`**（`ACTIVITY_POOL` 第 10 項，`dream_event.py:230`）。

### 1.7 🔴 與 SI-2.1 防線的關係（明確標示）

**grep 範圍**：`PrivacyVisibilityGate|ProducerGate|IdentityFirewall|SI-2.1`（`src/**`，90 處命中，全部位於 `src/social/**`、`src/inner_life/**`、`src/world/middleware.py`）。

| SI-2.1 防線 | 實作位置 | 守的資料流 | **是否在 diary / `shareable` 路徑上？** |
|------------|---------|-----------|--------------------------------------|
| 防線 1 Ambient Perception Path | `src/world/middleware.py:62,259-288,583-658` | 訂閱 `SOCIAL_WORLD_EVENT` → 渲染 `[社交感知]` | ❌ **不在** |
| 防線 2 Privacy Visibility Gate | `src/social/producer_gate.py:65`（`SocialEventProducerGate`） | **發布端**：私密內容不得上 social bus（`PUBLIC_CHANNELS` 白名單 `:45`） | ❌ **不在** |
| 防線 3 Identity Firewall | `src/social/identity_firewall.py:43`，由 `src/inner_life/submission_gate.py:333` 第 6 步呼叫 | **消費端**：`actor_id != current_agent_id` → `EXTERNAL_OTHER_ACTION` 不可內化 | ❌ **不在**（`SubmissionGate` 守的是 elevation 內化，`submission_gate.py:180`） |
| SG-3 §7.3 INV-9 fail-closed | `src/soul/dream_event.py:522-545, 625-634` | **只守 `impression_tags` → relationships 寫入**（`source_mode=="private"` / `is_private=True` / TG 1:1 → 不產生 tag） | ⚠️ **同檔但不同欄位**：日記 entry 本身**完全不受此閘門影響** |

**🔴 判定（逐字回答）**：

1. **`shareable` 的寫入端與判定端，皆不涉及任何 SI-2.1 防線。** 放寬 `shareable` **不會穿透防線 1／2／3 中任何一道**（因為沒有任何一道在 diary 寫入或讀取路徑上）。
2. **`shareable` 不是隱私標記**，把它當隱私閘門使用是**誤讀**（`dream_event.py:219` 的註解已自承語意是「值得分享」）。
3. **⚠️ 但放寬仍有一個真實的隱私風險，且它是「未設防」而非「穿牆」**：
   - `write_shared_event`（`dream_event.py:734-806`）與 cross_chat（`scheduler.py:1039-1095`）產生的 diary `content`，內容**來自兩個角色之間的互動**（例：`data/soul/interactions.jsonl` 的 `shared_event` 記錄「今天和 agent_yua 一起做了 創作」）。
   - **diary entry 沒有任何欄位標記這件事的來源通道**（無 `channel` / `is_private` / `visibility`）。`is_private` 參數存在但**只用於 `impression_tags`**（`dream_event.py:625-634`），**不寫進 entry**。
   - 因此：放寬 `shareable` 會**擴大一個目前無任何 SI-2.1 防線覆蓋的暴露面**（A2A 衍生內容 → 進 Bryan 私聊 prompt）。這正是 SPEC §11.3-5 所標的「實作票首要前置」——本調查**證實該前置仍然存在且未解**。
   - **本票不建議把此風險描述為「穿透 SI-2.1」**；正確描述是「**放寬會讓一個未設防的來源標記缺口變得有實際後果**」。

---

## §2 (b) 所有現存「活動」來源窮舉與真實產率

### 2.1 產率盤點表（7D 窗口 ＝ 70 agent-day；POST-M7-1 ＝ 235 agent-day）

| 代號 | 來源 | 資料位置 | 寫入者（`path:line`） | **7D 行數** | **≈每角色每日** | 語義內容？ | 隱私標記 | **已入 prompt？** |
|------|------|---------|----------------------|-----------:|----------------:|-----------|---------|------------------|
| **A-1** | `slot=="event"` ∧ `shareable:true` ∧ `source=="llm"` | `data/soul/{agent}/diary/YYYY-MM-DD.jsonl` | `dream_event.py:_write_entry:499`（`write_event:669`） | **7** | **0.100** | ✅ 有（`activity`/`category`/`content`） | ⚠️ `shareable`（**非隱私**，見 §1.2） | ⚠️ **間接**：`proxy.py:250-312` 讀近 3 日 slot∈{morning,night,dream,event}∧`source=="llm"`（**但 event 與 morning/night/dream 混雜、無優先序、上限 5 條**）→ 注入 `proxy.py:719-725`（group）／`:1353-1359`（private）。**M7-2 的 4 條件 reader（`scheduler.py:1189-1244`）產出只進 `extra`，不進 prompt** |
| **A-2** | `slot=="event"` ∧ `source=="llm"`（不限 `shareable`） | 同上 | 同上 | **9** | **0.129** | ✅ | 同上 | 同上 |
| **A-3a** | `slot=="morning"` ∧ `source=="llm"` | 同上 | `src/soul/diary.py`（經 `AgencyTriggerHandler`/`DiaryHandler`，`scheduler.py:1490-1493`） | **50** | **0.714** | ✅ 敘事痕跡 | ❌ 無任何欄位 | ✅ `proxy.py:293` 白名單 → `:719-725`／`:1353-1359` |
| **A-3b** | `slot=="night"` ∧ `source=="llm"` | 同上 | 同上 | **59** | **0.843** | ✅ | ❌ 無 | ✅ 同上 |
| **A-3 合計** | morning＋night ∧ `source=="llm"` | 同上 | 同上 | **109** | **1.557** | ✅ | ❌ 無 | ✅ |
| **A-4** | `slot=="dream"` ∧ `source=="llm"` | 同上 | `dream_event.py:write_dream:514-606` | **28** | **0.400** | ✅（但語意是「夢到誰」＝內在活動，非「剛剛在做什麼」） | ❌ 無（`is_private` 只作用於 tags） | ✅ `proxy.py:293` |
| **A-5** | goals 表 | `data/memory/{agent}/graph.sqlite` → `goals` 表（14 欄，`src/goals/models.py:117-134`） | `src/goals/seed_provider.py:~190`（`GraphStore.transition_goal`） | **33 筆**（`created_at ∈ 09-07..09-13`）<br>全表 **54 筆** | **0.471** | ✅ **有**（`title`/`description` 自由文本，例：`「那場雨剛開始的時候，Bryan 最近還在。」`） | ❌ 無 | ❌ **0 處**（`proxy.py` 全段無 `goals`/`GraphStore` 讀取 → **SPEC G2 確認成立**） |
| **A-5′** | goals 中 `state ∈ {ACTIVE, IN_PROGRESS}` | 同上 | 同上 | **0** | **0.000** | — | — | ❌ |
| **A-6** | inner_life trace | `data/inner_life/trace.jsonl`（**全體共用單檔**） | `src/inner_life/writer.py`（NarrativeTraceWriter） | **784** | 11.2（**總量**，非 per-agent） | ❌ **只有標籤**：`provenance.trigger_type` 例 `world:news_event`；**無 `agent_id` 頂層欄位**（只有 `provenance.actor_id`，world 事件為 `null`） | ❌ 無 | ❌ **0 處**（`proxy.py` 未使用 `trace_reader` → **SPEC G3 確認成立**；相符於 SPEC OQ-4 排除決定） |
| **A-7** | interactions（A2A） | `data/soul/interactions.jsonl`（**全體共用單檔**） | `scheduler.py:1024-1034`（`_append_interaction`） | **7** | **0.100**（總量；per-agent 更低） | ✅ 有（`type` ∈ {`shared_event`, `cross_chat`}、`agents[]`、`activity`、`content`） | ❌ 無 | ❌ 無 |
| **A-8** | world perception | `data/world/perception_trace.jsonl` | `src/world/*` | **1523** | 21.8（總量；**跨角色共用**） | ⚠️ 只有世界事件（`news_event` 4932 / `rain_started` 3071 / `weather_temp_change` 2702 全期） | ❌ 無 | ✅ **已入**（`proxy.py:1383-1384` `world_context`）——但**不是角色自己的活動** |
| **A-9** | motive_trace | `data/soul/motive_trace.jsonl` | `src/soul/motive.py` / `decision.py` | **25** | 0.357（總量；ruka 39/47 全期） | ✅ 有（`content` 動機文本） | ❌ 無 | ⚠️ 間接：經 `motive_content` 進 Decision，不直接入 private prompt |
| **A-10** | decision_trace | `data/soul/decision_trace.jsonl`（**14 行**） | `scheduler.py:_decision_check` | **14**（全在 09-13） | — | ❌ `agent_id` 集中 rem(12)/akane(1)/yua(1)、`reason ∈ {test,x,decision_llm_failure_or_bad_output}` | ❌ | ❌ | **⚠️ 測試污染，不可用於統計（SPEC ND-1 已記載，本票遵循不採用）** |
| **A-11** | elevation nodes/edges | `data/elevation/elevation_*.jsonl` | `src/inner_life/elevation_adapter.py` | **行內無 `ts` 欄位 → 無法切窗** | **NO DATA** | ✅ 有（`default` 2039 筆 ＋ 10 隻各 ~45-100 筆） | ❌ | ⚠️ 經 `emergent_projection`／`epistemic_appraisal`（`proxy.py:1094,1112`）非直接 |
| **A-12** | emotion / inner state | `data/agents/{agent}/emotional-state.json`、`carryover.json` | `src/agent/emotion.py` | 檔案 mtime **08-07 / 09-10**（非日更） | **NO DATA** | ✅ 有 | ❌ | ✅ `[情緒狀態]` `proxy.py:1324-1326` |
| **A-13** | ACTIVITY_POOL（靜態池本身） | `src/soul/dream_event.py:220-231` | 靜態常數（**0 寫入**） | **10 筆常數** | — | ⚠️ **只是標籤**（活動名），**不含任何事件內容** | ⚠️ 池中帶 `shareable` | ❌ 不直接入 prompt；僅經 `write_event` 的 LLM system prompt（`dream_event.py:672`） |

### 2.2 已進入 prompt 的來源（逐項 `path:line`）

| 來源 | prompt 位置 | 進入條件 |
|------|-----------|---------|
| diary（morning/night/dream/**event**） | `src/llm/proxy.py:250-312`（reader）→ **`:719-725`**（group）／**`:1353-1359`**（private, `## 你的最近內在生活`） | `slot ∈ {morning,night,dream,event}`（`:293`）∧ `source == "llm"`（`:301`）∧ content 非空（`:304`）；近 **3** 日（`INNER_LIFE_DAYS`，`:135`）、上限 **5** 條（`:136`）、每條截 **60** 字（`:137`） |
| world perception | `proxy.py:1383-1384` | 無條件 |
| emotion | `proxy.py:1324-1326` | 無條件 |
| elevation / emergent | `proxy.py:1094`（`format_emergent_block`）、`:1112`（`format_epistemic_horizon_delta`） | 條件式 |
| **goals** | **❌ 無** | — |
| **inner_life trace** | **❌ 無** | — |
| **interactions（A2A）** | **❌ 無** | — |
| **motive_trace** | 間接（決策層，非 private system prompt） | — |

> **🔴 一個關鍵落差**：`_format_recent_inner_life` 讀的是「**近 3 日、混 slot、上限 5 條、按行序取最後 5 條**」（`proxy.py:312` `out_lines[-INNER_LIFE_MAX_ENTRIES:]`），**沒有「今日優先」也沒有「最近數小時優先」**；且 event 只佔其中 9/149 ≈ 6%。**故「錨 A 已存在」的說法只對「有日記痕跡」成立，對「剛剛在做什麼」不成立**（與 SPEC G1 一致）。

### 2.3 未被納入的來源與原因（逐項）

| 來源 | 未納入原因（本票實測支持） |
|------|--------------------------|
| A-5 goals | ① **現況 `ACTIVE`/`IN_PROGRESS` = 0**（54 筆 100% `SUSPENDED`）② `proxy.py` 0 引用 ③ 語意是「未解的念頭／疑問」多於「剛才在做什麼」（清單見 §3.4 註）④ 讀 sqlite 成本高於讀 jsonl |
| A-6 inner_life trace | ① **無 `agent_id`**（跨角色混流）② 只有 `trigger_type` 標籤、**無可讀活動內容** ③ 符合 SPEC OQ-4 排除 |
| A-7 interactions | 量太小（7D = 7 行全體）＋ **來源通道未標記**（§1.7 風險） |
| A-8 world perception | 已是 `[世界感知]`；語意是「世界發生什麼」非「我在做什麼」 |
| A-10 decision_trace | **測試污染**（14 行中 12 行 `agent_rem` `reason=test/x`） |
| A-11 elevation | 無法按時間切窗（行內無 `ts`） |

---

## §3 (c) 理論供給缺口計算

### 3.1 🔴 實測 `proactive_dm` 發訊率（**不用規格書的 5–8 條/日假設**）

**量測基礎**：`data/logs/server_*.err` ＋ `data/logs/server_nohup*.err` ＋ `data/logs/server_*.log` ＋ `data/server_nohup.err`（**去重後 206 個檔案**）。

**⚠️ 覆蓋率必須先講清楚（否則會誤讀下面的比率）**：把 206 個檔案的 `[首行時間, 末行時間]` 區間合併後，得到 **84 段不連續區間、總計 152.8 小時**，而 wall-clock span 是 **1411.8 小時**（`2026-07-16 17:04` ～ `2026-09-13 12:55`）→ **日誌覆蓋率僅 10.8%**。詳細覆蓋表見 §7.3。

→ **因此本節的觸發「次數」可信（每次觸發都有明確 log 行），但「比率」的可外推性受限**：32 次觸發分佈在 152.8 覆蓋小時內 ＝ **0.21 次/覆蓋小時 ≈ 5.0 次/覆蓋日**；而**送達 0 次/覆蓋日**。斷檔期間不可知，已列 ND-1。

```powershell
# 可複驗：去重後統計
# 「proactive_dm 觸發」= 32 行；「[M7-2]」= 0 行；「proactive_dm THROTTLED」= 7 行；「不可送達」= 10 行
```

**（a）觸發與結果分解（32 筆去重觸發，`2026-07-30` ～ `2026-09-12`）**：

| 結果 | 筆數 | 判定依據（log 逐字） | **實際送出** |
|------|-----:|---------------------|-------------:|
| `LONGING_BLOCK`（想念 < 0.3） | **12** | `[M7-longing] agent_ruka 想念 0.25 < 0.3, 排 30 min 後再查` | **0** |
| `PASS_THEN_THROTTLED` | **7** | `[M7-longing] … >= 0.3, 觸發主動傳訊` → `[ChannelRouter] proactive_dm THROTTLED \| hours_since=… > 4.0h` | **0** |
| `LONGING_PASS` → Decision 拒絕 | **2** | `>= 0.3, 觸發主動傳訊` → `[SM-3 Decision] agent_ruka not_transmit … reason='…不適合主動傳訊打擾'` | **0** |
| `UNKNOWN`（M7-longing 前，日誌格式不同） | **11** | `2026-07-30 ～ 2026-08-02`（舊 3-5h timer 時代） | **NO DATA** |
| **合計** | **32** | | **0（可觀測窗口內）** |

**（b）當前生產窗口（`2026-09-12 22:39:13` ～ `2026-09-13 12:50:03`，共 14.18h）逐筆**：

```
10 × 「💬 proactive_dm 不可送達: Bry 最後看見 11.1h～15.6h 前 > 4.0h, skip (不 publish AGENCY_TRIGGER、不觸發 LLM)」
  時刻：08:09:44 / 08:39:46 / 09:09:47 / 09:39:50 / 10:09:51 / 10:39:54
        11:09:56 / 11:39:58 / 12:10:00 / 12:40:02
proactive_dm 實際觸發（publish AGENCY_TRIGGER）= 0
proactive_dm 實際送出訊息 = 0
（23:09 ～ 08:00 落在靜音時段 23:00-08:00，該分支為 logger.debug → INFO 級日誌不記錄，故無行）
```

**（c）🔴 三個決定性事實**：

1. **`[M7-2]` 在所有日誌中出現次數 = 0** → **活動驅動的 enrichment 分支（`scheduler.py:1459-1469`）自上線以來，從未在生產中執行過一次。**（因為它排在 `難想念` 與可送達檢查之後，前置閘門從未同時放行。）
2. **白名單只有 1 隻角色**：`proactive_whitelist=['agent_ruka']`（`scripts/run_server.py:1071` `proactive_agents=["agent_ruka"]`；生產日誌 `server_nohup.err:116` 與每次觸發行皆載明）。**10 隻角色中只有 ruka 有資格觸發**。
3. **「5–8 條/日」是啟動 banner 的靜態文案，不是實測值**：`server_nohup.err:116`「proactive_dm 3-5h ≈ 5-8 條/天 (Bry 拍板 8/6 17:12)」，但 M7-longing（`scheduler.py:1437-1455`，2026-08-18）已把定時器改成「想念門檻 ＋ 30min 複查」，`scheduler.py:1398` 的程式註解亦自承「8/19-8/29 共 23 次 proactive_dm 觸發…然後被 router M0.5 THROTTLED 丟棄」。

**→ 實測結論：可觀測窗口內 `proactive_dm` 實際送達訊息數 = 0。** 缺什麼觀測見 §4 ND-1。

### 3.2 需求側定義（三種口徑，避免用假設掩蓋事實）

| 口徑 | 定義 | 數值 | 來源 |
|------|------|-----:|------|
| **D1（實測）** | 可觀測窗口內實際送達 | **0 條/日** | §3.1 |
| **D2（設計目標）** | 啟動 banner 宣稱 | **5–8 條/日**（全體，皆為 ruka） | `server_nohup.err:116` |
| **D3（程式常數上限）** | 清醒 16h ÷ 最小間隔 180min（`scheduler.py:141`）＋ 2h 冷卻（`:143`）＋ 靜音 8h（`:1140`） | **≈ 5 條/日**（全體，皆為 ruka） | `scheduler.py:141-143,1140` |

**「每輪 transmit 至少 1 行錨 A」的等價需求**：**ruka 每日需要 ≥ 1 條「今日、4 條件全過」的 event entry**（若 D2 成立則需 5–8 條；若 D3 則需 ~5 條）。

### 3.3 供給 vs 需求 → 缺口

| 供給源（僅算可成為錨 A 者） | ruka 實測 | 全角色實測 | 每角色每日 |
|---------------------------|----------:|-----------:|-----------:|
| **A-1（現行 4 條件，POST-M7-1 24 agent-day）** | **1 筆** | 9 筆 / 235 agent-day | **0.038** |
| A-1（7D 窗口） | 1 筆（09-07） | 7 筆 / 70 agent-day | **0.100** |
| A-2（放寬 `shareable`，POST-M7-1） | 4 筆（`eventllm`，ruka） | 15 筆 / 235 agent-day | **0.064** |
| A-3（morning＋night，7D） | — | 109 筆 / 70 agent-day | **1.557** |
| A-4（dream，7D） | — | 28 筆 / 70 agent-day | **0.400** |
| A-5（goals，7D `created_at`） | — | 33 筆 / 70 agent-day | **0.471** |
| A-7（interactions，7D） | — | 7 筆 / 70 agent-day | **0.100（全體總量）** |

**🔴 缺口計算（以「每角色每日需要 ≥ 1 條」為最低標準）**：

```
需求（最低）：ruka ≥ 1 條/日        （D3 目標下：≥ 5 條/日）
供給（現行）：ruka 0.038 條/日      （= 1 筆 / 24 agent-day，POST-M7-1）
──────────────────────────────────────────────
缺口（最低標準）：  ≈ 0.96 條/日  →  供給僅達需求的 3.8%    （≈ 26 倍不足）
缺口（D3 目標）：   ≈ 4.96 條/日  →  供給僅達需求的 0.8%    （≈ 131 倍不足）
```

**逐條件放寬的邊際效益（證明「只放寬 `shareable` 不夠」）**：

| 放寬動作 | ruka 供給（每角色每日） | 相對現行 | 是否達到最低需求？ |
|---------|----------------------:|--------:|------------------|
| 現行（4 條件） | 0.038 | 1.0× | ❌ |
| 只放寬 `shareable` | **0.167**（= 4/24） | **4.4×** | ❌ 仍差 6× |
| 只放寬「今日檔」→ 近 3 日 | ≈ 0.114（3 日窗內 4 筆/35 日窗） | 3.0× | ❌ |
| 放寬 `slot` → 加入 morning/night | **≈ 1.71**（ruka `mn=41`/24 日） | **45×** | ✅ **唯一單獨達標者** |
| 放寬 `slot` ＋ `shareable` ＋ 今日檔 | 1.83 | 48× | ✅ |

> **→ 定量結論：錨 A 的缺口主要來自「來源維度」而非「`shareable` 布林值」。放寬 `shareable` 只能把供給提升 4.4 倍，仍不足；必須同時引入 A-3（morning/night）這個唯一量級足夠的來源。**

### 3.4 候選方案對比（≥3 個，含建議）

| # | 候選方案 | 可補多少量（每角色每日） | 隱私語意 | 需新增 schema？ | 觸 Frozen Contract？ | 成本 | 本票評估 |
|---|---------|------------------------:|---------|----------------|---------------------|------|---------|
| **C-1** | **放寬 `shareable`**：判定改為 `slot=="event" ∧ source=="llm"`（`scheduler.py:1226` 移除） | 0.038 → **0.167**（+4.4×） | ⚠️ 讓渡「值得分享」語意（**不涉隱私判定**，§1.2） | ❌ 0 | ❌ 0（不在 §9 清單） | 1 行改動；**0 新 LLM 呼叫** | **必要但不充分**：單獨做仍差 6× |
| **C-2** | **加入 A-3（morning/night ＋ `source=="llm"` ＋ 今日）為主要供給**，A-1 保持最高優先序（SPEC §2.1.2 已拍板之設計） | **+1.557** → 合計 ~1.7 | ❌ 無新增（日記無隱私欄位） | ❌ 0 | ❌ 0（`format_activity_anchor` 為純新增唯讀函式，SPEC §2.1.3） | 1 個唯讀投影函式；**0 新 LLM 呼叫**（INV-TG-4） | ✅ **唯一單獨達標且零隱私代價的方案**；**建議作為主體** |
| **C-3** | **時效窗由「今日檔」放寬為「近 N 日（N=1..3）」**，並以既有三格時間前綴（SPEC §2.1.3）承載鄰近性 | A-1 由 0.038 → **0.114**（N=3）；與 C-2 疊加後 ~1.75 | ⚠️ 需處理「A2A 衍生內容無來源標記」（§1.7） | ❌ 0（前綴表 SPEC 已定稿） | ❌ 0 | 路徑迴圈 3 次（`scheduler.py:1211` 同一寫法） | ✅ 建議**作為 C-2 的補強**（解決「今日檔」這道致命閘門），但**單獨做不夠** |
| **C-4** | **啟用 A-5（goals `title`）為兜底來源** | **+0.471**（`created_at` 近 7 日） | ❌ 無新增，但需守 No-Scoring（只取 `title`，0 數值欄位） | ❌ 0（表已存在 14 欄） | ⚠️ **需主大腦判**：新增 `proxy.py` 讀 sqlite 路徑 = 新 I/O 面 | 每輪 1 次 sqlite 唯讀查詢（`GraphStore` 已在用）；**0 新 LLM 呼叫** | ⚠️ **條件式可行**：目前 `ACTIVE`=0（全 SUSPENDED），但 `motive_provider.py:703-708` 的喚醒條件 ③ 是「Bry last_seen < 4h」——**恰與 proactive_dm 的 4h 閘門同源**，即「Bry 在場時 goals 會被喚醒」。**需先取得喚醒行為的生產實測（見 ND-3）**。語意上偏「未解的念頭」而非「剛在做什麼」，保真度次於 C-2 |
| **C-5** | **提高 `ACTIVITY_POOL` 的 `shareable` 比例**（6/10 → 10/10） | 0.064 → **0.106**（僅作用於 A-2 之上） | ⚠️ **實質摧毀 `shareable` 語意**（欄位退化成恆真） | ❌ 0 | ❌ 0 | 改 4 行常數 | ❌ **不建議**：與 C-1 效果等價但語意破壞更嚴重（C-1 至少保留「寫入端曾判定」的事實） |
| **C-6** | **提高 event 產率**（現 4–8h，`scheduler.py:1497-1499`） | 線性放大 A-1：4–8h → 2h 約 **3.4×** → 0.129（`event/llm`） | ❌ 無新增 | ❌ 0 | ⚠️ 動 trigger 節奏，需主大腦判 | **每次 event = 1 次真實 LLM 呼叫**（`dream_event.py:681`）；10 隻角色成本 ×3.4 | ⚠️ **成本最高**且仍達不到 1.0/角色/日（0.13）；**不建議單獨採用** |
| **C-7** | 啟用 A-7（interactions） | **+0.100**（全體總量，per-agent ≈ 0.01） | 🔴 **有實質隱私風險**（A2A 衍生內容無來源標記，SPEC §11.3-5 未解） | ⚠️ 需新增來源通道欄位才有辦法守 INV-9 | ⚠️ 可能觸 SG-3 INV-9 | 低 | ❌ **不建議**（量太小、風險最大） |

### 3.5 建議（附理由）

**建議組合：`C-2（主體）＋ C-1（保真優先序內的小幅放寬）＋ C-3（時效窗補強）`，並把 `C-4` 列為第二階段候選。**

理由（依重要性排序）：

1. **C-2 是唯一「單獨就能達標且零隱私代價」的方案**：+1.557 條/角色/日，直接覆蓋最低需求（1.0）與 D3 上限（~5.0）的缺口主體。且 `source=="llm"` 閘門保留，故不引入 placeholder 風險——這正是 SPEC OQ-1 建議的 (b) 方向。
2. **C-1 保留在優先序中而非刪除**：它只讓渡「值得分享」語意（§1.2 證明與隱私無關），代價 1 行改動、0 新成本，且在 A-1 命中時提供最高保真度（真實性＋可分享雙閘門）。
3. **C-3 必要**：若不處理「今日檔」，則「近 7 日有 7 筆合格 entry」中的 4 天仍然是 0（§1.5 排名②）。三格時間前綴（SPEC §2.1.3 已定稿）已提供不虛構的鄰近性表達，故放寬時效窗**不新增虛構風險**。
4. **不建議 C-5／C-6／C-7**：C-5 語意破壞比 C-1 更重且效益更低；C-6 成本線性上升（真實 LLM 呼叫）卻仍無法達標；C-7 量級可忽略而隱私風險最大。
5. **C-4 需先補觀測**：goals 目前 100% `SUSPENDED`，其喚醒條件（`motive_provider.py:703-708` ③`Bry last_seen < 4h`）未經生產實測（§4 ND-3），且語意（未解念頭）與「剛剛在做什麼」有落差。列第二階段。

**🔴 但必須同時聲明（誠實記載，不為樂觀答案鬆動標準）**：

> **在「現行交付鏈」下，錨 A 供給不是當前的綁定約束（binding constraint）——`proactive_dm` 實測送達量為 0（§3.1）。**
> **因此「每句話都有扎實生活由來」目前在數量上是一個空集合命題：沒有訊息，就無從檢驗。**
> 若只做供給補強而不修交付鏈，Owner 的需求仍不會被滿足（訊息數仍為 0）。
> **正確認知順序是：① 先讓交付鏈可送達（4h 閘門／longing 門檻／Decision／throttle 四道中至少一道放行）→ ② 同步把 A-1 的供給換成 A-2/A-3（本節建議）→ ③ 驗收「每輪 transmit 帶幾個錨」。**

---

## §4 NO DATA 清單（逐項說明缺什麼觀測）

| # | 項目 | 為何量不到 | 缺什麼觀測 |
|---|------|-----------|-----------|
| **ND-1** | **可觀測窗口外的 `proactive_dm` 實際送達數** | 206 個去重日誌檔合併後只有 **84 段不連續區間、共 152.8h**（wall span 1411.8h）→ **覆蓋率僅 10.8%**，且有 9 個日期完全無日誌（§7.3）；另 32 筆觸發中有 11 筆（07-30～08-02）落在 M7-longing 之前，日誌格式不同無法分類 | 需要**連續、不中斷的生產日誌**，或一項**只讀的送出計數器**（例如 `data/state/outbox.json` 的 append 統計）。本票**不建工具** |
| **ND-2** | **`outbox.json` 是否曾承接被 throttle 的 proactive_dm** | `data/state/outbox.json` 於本次量測中**未被納入統計**（本票僅讀 `data/state/` 清單，未解析其內容以避免擴大資料讀取範圍） | 需一項只讀檢查（讀 `data/state/outbox.json` 的條數與時間分布） |
| **ND-3** | **goals 的 `SUSPENDED → ACTIVE` 喚醒行為在生產中的實際發生率** | 本票為**單次快照**（`2026-09-13T12:52`），觀察到 54 筆全部 `SUSPENDED`；喚醒是寫入操作（`transition_goal`），本票不得執行 | 需**連續多時點的唯讀快照**（或 `state_updated_at` 的歷史軌跡）。**注意：`state_updated_at` 只保留當前值，會覆寫**，故歷史軌跡需另立落盤 |
| **ND-4** | **`elevation_*` 的 7 日行數** | 行內**無任何可辨識時間戳欄位**（`ts`/`timestamp`/`created_at` 皆缺），無法切窗 | 需 elevation 落盤補時間戳，或改用檔案 mtime 分層統計（本票不做，因 mtime 會被整檔重寫影響） |
| **ND-5** | **`capability_projection_trace.jsonl`（841 KB）的內容與產率** | 本票未解析（超出錨 A 供給範圍） | 需一項專項盤點；本票判定它與「活動由來」語意距離最遠 |
| **ND-6** | **實際 transmit prompt 全文（含真實注入的錨行）** | 觀察期內**不得**對 `:8000` 送請求、不得重啟 | 同 SPEC ND-4；需在允許窗口以既有觀測通道取樣 |
| **ND-7** | **`agent_newcomer` 的 diary 是否屬生產角色** | 它有 `diary/` 目錄但無 `relationships.json`，本票以「10 隻在線角色」為分母，未單獨論述其角色定位 | 需 config 側確認（`configs/packs/roswaal_mansion_v1.yaml`）之名冊 |

---

## §5 結論一句話

> **Owner 的「每句話都有扎實生活由來」在現行資料下「需先補供給、且需先修交付鏈」——不是「不可能」；但僅放寬 `shareable` 絕對不夠（供給只從 0.038 升到 0.167 條/角色/日，仍差 6 倍），必須把 A-1（event 四條件）換成以 A-3（morning/night，1.557 條/角色/日）為主體、A-1 為最高優先序的階梯式投影（即 SPEC §2.1.2 已拍板的設計）；同時前置條件有三：① `proactive_dm` 交付鏈目前在生產中送達量為 0（4h 閘門 ＋ longing 門檻 ＋ Decision ＋ M0.5 throttle 四道全數擋下），不修則「每句話」這個命題無標的可驗；② A2A 衍生內容缺來源通道標記（SPEC §11.3-5），放寬任何供給前必須先解，否則會讓一個目前無 SI-2.1 覆蓋的暴露面變成有實際後果；③ 生產日誌有斷檔，`proactive_dm` 的真實長期發訊率仍需連續觀測（ND-1）。**

**「阻擋大戶」一句話**：**`slot=="event"` 是結構性大戶（篩掉 >90%），「今日檔」是決定性致命閘門（把 7 日殘量在需要時歸零），`shareable` 只是今日的直接卡點（近期僅篩掉 22%）。**

---

## §6 紅線遵守與證據

### 6.1 `data/soul/**` 與 `data/faulthandler.log` 前後對照（證明零寫入）

**量測方法**：工作開始前（`2026-09-13T12:52` 前）對 `data/soul` 全樹 468 個檔案做 `(Path, Size, LastWriteTime)` 快照存至 `$env:TEMP\anchorA_soul_before.csv`；工作結束後重掃比對。

| 項目 | 前（BEFORE） | 後（AFTER） | 差異 |
|------|-------------|------------|------|
| `data/soul/**` 檔案數 | **468** | **468** | **0** |
| 內容變更（size 或 mtime 不同） | — | — | **0** |
| 新增 | — | — | **0** |
| 刪除 | — | — | **0** |
| `data\faulthandler.log` size | **6,373,318 B** | **6,373,318 B** | **0** |
| `data\faulthandler.log` mtime | `2026-09-12T22:38:16.5814102-04:00` | `2026-09-12T22:38:16.5814102-04:00` | **0** |

`data\soul\` 頂層四個檔案的 mtime **全部早於本票開始時間**（皆為 `2026-09-12T22:40` 或 `2026-09-13T08:09`），證明本票期間**生產亦未寫入 `data/soul`**：

```
capability_projection_trace.jsonl  841493B  2026-09-12T22:44:35.3503632-04:00
decision_trace.jsonl                 3570B  2026-09-12T22:44:35.6388916-04:00
interactions.jsonl                   6482B  2026-09-13T08:09:44.3824816-04:00
motive_trace.jsonl                  18071B  2026-09-12T22:40:59.1348887-04:00
```

**✅ `data/faulthandler.log` 未被開啟、未被讀取、未被觸碰。**

### 6.2 四埠 PID 對照 ＋ 零重啟聲明

| 埠 | 工作前 PID | 工作後 PID | StartTime | 一致？ |
|----|-----------|-----------|-----------|-------|
| 8000 | **2312** | **2312** | `2026/9/12 下午 10:39:13` | ✅ |
| 8765 | **9576** | **9576** | `2026/9/12 下午 07:21:18` | ✅ |
| 8766 | **9592** | **9592** | `2026/9/12 下午 07:21:18` | ✅ |
| 8767 | **9584** | **9584** | `2026/9/12 下午 07:21:18` | ✅ |

> **聲明：本票零重啟、未干擾 24 小時穩定性觀察期。** 未執行任何 `server_ops` / watchdog / `Stop-Process` / `Start-Process` / restart 動作；未對任何埠送出請求。生產正值觀察期（`:8000` pid 2312，StartTime `2026-09-12T22:39:13`），全程未受影響（本票期間生產日誌持續正常輸出至 `12:50:03`）。

### 6.3 是否踩紅線／意外行為

| 紅線 | 狀態 | 證據 |
|------|------|------|
| 不得改動 `src/**`、`scripts/**`、`tests/**`、`configs/**`、`data/**` | ✅ **未踩** | `git status` 顯示本票只新增 1 個 docs 檔（§7）；`data/soul` 468 檔 0 變更（§6.1） |
| 不得寫入 `logs/ENGINEERING_STATE.md` | ✅ **未踩** | 該檔未出現於本票 diff |
| 不得重啟／干擾 `:8000` 與 `:8765/8766/8767` | ✅ **未踩** | 四埠 PID 前後一致（§6.2） |
| 不得跑任何測試 | ✅ **未踩** | 未執行 `pytest` / `unittest` / 任何 test 檔案 |
| `data/**` 全程唯讀；**不得開啟 `data/faulthandler.log`** | ✅ **未踩** | size/mtime 前後一致（§6.1） |
| 不得編造；量不到寫 NO DATA | ✅ **遵守** | §4 列 7 項 ND；§3.1 明示「實測 0 條/日」而非沿用 5–8 假設 |
| 禁止用測試資料推論生產率 | ✅ **遵守** | `decision_trace.jsonl`（14 行，12 行測試污染）**僅標示為污染，未納入任何產率計算**（§2.1 A-10） |
| commit 只 `git add docs/ANCHOR-A-SUPPLY-INVESTIGATION.md` | ✅ **遵守** | 見 §7 |
| 遇衝突立即停止回報 | ✅ 未發生衝突 | 見 §7 |

**⚠️ 意外行為（1 項，已揭露，非紅線）**：
工作期間 PowerShell 5.1 的預設 ANSI 編碼導致首次全樹 JSON 解析只有 148/1098 行成功（§0.2）。此為**唯讀量測的假陰性**，未造成任何寫入，且已改用 `-Encoding UTF8` 修正後重跑全部統計。**此陷阱已寫入 §0.2 供後續工單沿用。**

**⚠️ 一項限制（非紅線，已揭露）**：
為讀取 goals 表，本票以 Python `sqlite3.connect("file:...graph.sqlite?mode=ro", uri=True)` 直接唯讀開啟 `data/memory/{agent}/graph.sqlite`。**未複製、未建立 `-wal`/`-shm`、未執行任何寫入語句**（僅 `SELECT`）。此為唯一觸及 `data/**` 二進位檔的讀取，且為 `mode=ro`。

---

## §7 附錄：量測可複驗命令與日誌覆蓋表

### 7.1 環境與 HEAD

```
pwd    → C:\Users\bbfcc\.local\bin\soul-os-harness
git log --oneline -3
  da50452 docs: sync Current HEAD to 566a444      ← 起始 HEAD（符合工單）
  566a444 docs: add TRANSMIT-GROUNDING-SPEC (3-anchor transmit grounding, awaiting approval)
  4146218 docs: sync Current HEAD to a521645
pwsh   → 5.1.26100.9444  (⚠️ 見 §0.2 編碼陷阱)
```

### 7.2 核心量測命令（全部唯讀，可逐條複驗）

```powershell
# ① 全樹 diary 逐行解析（必須 -Encoding UTF8）→ 1098 行 / 0 解析失敗
$files = Get-ChildItem 'data\soul' -Recurse -File -Filter *.jsonl |
         Where-Object { $_.FullName -match '\\diary\\' }              # 442 檔
foreach($f in $files){ foreach($l in (Get-Content $f.FullName -Encoding UTF8)){ $null=$l|ConvertFrom-Json } }

# ② 漏斗（WHOLE）
# N0=1098 → slot=event 100 → +shareable:true 17 → +source=llm 9 → +activity 9 → +今日檔 0

# ③ 7 日窗口（依檔名日期，非 mtime）
# 檔名 2026-09-07..2026-09-13 → 70 檔 / 149 行
# N0=149 → 9 → 7 → 7 → 7 → 0

# ④ shareable 值態分布（slot=event, n=100）：ABSENT 77 / TRUE 17 / FALSE 6

# ⑤ proactive_dm 觸發與結果（去重後）
# 「proactive_dm 觸發」32 / 「[M7-2]」0 / 「proactive_dm THROTTLED」7 / 「不可送達」10

# ⑥ goals 表（唯讀，mode=ro）
$env:PYTHONIOENCODING='utf-8'
@'
import sqlite3, glob
for q in sorted(glob.glob("data/memory/agent_*/graph.sqlite")):
    c=sqlite3.connect("file:"+q+"?mode=ro", uri=True).cursor()
    c.execute("select state, count(*) from goals group by state"); print(q, c.fetchall())
'@ | & '.venv\Scripts\python.exe' -
# → 10 個 DB 全部 [('SUSPENDED', N)]，合計 54 筆
```

### 7.3 日誌覆蓋表（`proactive_dm` 統計的分母透明度 —— **本票最弱的一環，誠實揭露**）

**合併後**：206 個去重檔案 → **84 段不連續覆蓋區間**，總計 **152.8 h** / wall span **1411.8 h** → **覆蓋率 10.8%**。

**唯四的「連續 ≥ 6 小時」區間**（其餘 80 段皆 ≤ 5h，多為 < 1h 的碎片）：

| 連續覆蓋區間 | 長度 | 期內 `proactive_dm 觸發` | 期內送達 |
|-------------|-----:|------------------------:|--------:|
| `2026-07-29 23:56` → `2026-07-30 11:22` | 11.45 h | 0（觸發在 19:14/20:57/22:59，落在此段之外） | 0 |
| `2026-08-01 20:54` → `2026-08-02 11:00` | **14.09 h** | **5**（皆 UNKNOWN，M7-longing 前） | **NO DATA** |
| `2026-08-16 22:23` → `2026-08-17 17:14` | **18.85 h** | **0** | 0 |
| **`2026-09-12 20:33` → `2026-09-13 12:55`（當前生產）** | **16.37 h** | **4**（全 `LONGING_BLOCK`）＋ 10 次 `不可送達` | **0** |

**逐日期覆蓋狀態**（✅＝當日有日誌；⚠️＝僅零散碎片；❌＝斷檔）：

| 日期 | 覆蓋 | 期內觸發 | 備註 |
|------|:----:|--------:|------|
| 07-16 | ⚠️ | 0 | 僅 5 段碎片（共 3.4h） |
| 07-17 ～ 07-28 | ❌ | — | **NO DATA** |
| 07-29 ～ 08-02 | ✅ | 11 | M7-longing 前，格式不同 → `UNKNOWN` |
| 08-03 ～ 08-06 | ❌ | — | **NO DATA** |
| 08-07 | ⚠️ | 0 | 單一 5 秒行 |
| 08-08 ～ 08-11 | ❌ | — | **NO DATA** |
| 08-12 ～ 08-14 | ⚠️ | 0 | 碎片（共 ~16h） |
| 08-15 ～ 08-17 | ⚠️→✅ | 0 | 含 18.85h 連續段，期內 0 觸發 |
| 08-18 | ✅ | 0 | 3 段（共 3.5h） |
| 08-19 | ✅ | 4 | 全部 `PASS_THEN_THROTTLED` |
| 08-20 | ❌ | — | **NO DATA** |
| 08-21 ～ 08-22 | ✅ | 5 | 3 `PASS_THEN_THROTTLED` ＋ 2 `LONGING_BLOCK` |
| 08-23 ～ 08-24 | ❌ | — | **NO DATA** |
| 08-24 22:33 ～ 08-25 00:33 | ✅ | 0 | 2h |
| 08-26 ～ 08-27 | ❌ | — | **NO DATA** |
| 08-28 ～ 08-29 | ✅ | 0 | 5 段碎片（共 ~1.2h） |
| 08-30 ～ 08-31 | ❌ | — | **NO DATA** |
| 09-01 | ✅ | 0 | 1 段（0.33h） |
| 09-02 | ❌ | — | **NO DATA** |
| 09-03 | ✅ | 1 | `LONGING_BLOCK` |
| 09-04 | ❌ | — | **NO DATA** |
| 09-05 | ✅ | 1 | `LONGING_PASS` → Decision `not_transmit` |
| 09-06 | ❌ | — | **NO DATA** |
| 09-07 ～ 09-08 | ✅ | 1 | 2 段（共 4.5h）；09-08 之 1 次 `LONGING_PASS` → `not_transmit` |
| 09-09 ～ 09-11 | ❌ | — | **NO DATA** |
| **09-12 ～ 09-13 12:55** | ✅ | **9** | 9 觸發（全 `LONGING_BLOCK`）＋ 10 次 `不可送達`；**送達 0** |

> **結論透明度聲明**：**32 次觸發中有 23 次（72%）落在覆蓋率僅 10.8% 的日誌內，另有 9 個日期完全無日誌。**
> 因此本票**不宣稱**「`proactive_dm` 的長期發訊率 = 0」，只宣稱「**在所有可觀測的 152.8 小時內（含唯一一次 16.37h 的當前生產連續段），送達數 = 0**」。
> 要得到長期比率，需**連續不中斷的日誌**或一項只讀送出計數器 → **ND-1**。**本票不為此建工具**（超出 READ-ONLY 授權）。

---

## 附錄 B：本票與 SPEC 的差分（只列本票新增，不重述 SPEC 已量測者）

| # | SPEC 的狀態（`566a444`） | 本票的補充 |
|---|------------------------|-----------|
| 1 | §11.3-4「`shareable: false` 的成因未追查」 | ✅ **已追查完畢**：唯一寫入 `dream_event.py:499`；語意 = 「值得分享」（**非隱私**）；成因二分（查表 false／欄位缺失）；今日 0/10 判定為 16% 機率的正常波動 ＋ 真正的瓶頸在 event 產率（§1.3、§1.6） |
| 2 | §1.4b「近 7 日 7 筆 ≈ 0.1/角色/日」 | ✅ **擴充為 4 窗口漏斗**（WHOLE/7D/TODAY/POST-M7-1）＋ **阻擋大戶判定**（§1.4、§1.5） |
| 3 | §2.1.1 A-5「goals：NO DATA」 | ✅ **已量測**：54 筆 / **100% `SUSPENDED`（`ACTIVE`=0）** / 7 日新增 33 筆 / `title` 有語意 / `proxy.py` **0 引用**（SPEC G2 確認）（§2.1、§2.3） |
| 4 | §2.1.1 A-6「inner_life trace：NO DATA」 | ✅ **已量測**：2340 行全期 / 784 行 7 日；**無 `agent_id`、只有 `trigger_type` 標籤** → 判定不可用（§2.1） |
| 5 | SPEC 未量測的「實測 proactive_dm 發訊率」 | ✅ **已量測**：32 觸發 / **實際送達 0** / `[M7-2]` 出現 0 次 / 白名單僅 ruka / 「5–8 條/日」為靜態 banner（§3.1） |
| 6 | SPEC 未做的「理論缺口計算」 | ✅ **已做**：需求 1（最低）～5（D3）條/日 vs 供給 0.038 → **缺口 26×～131×**；7 個候選方案對比 ＋ 建議（§3.3、§3.4、§3.5） |
| 7 | SPEC 未標示的「方法論陷阱」 | ✅ **已記載**：PowerShell 5.1 預設 ANSI 會讓 diary 逐行解析只成功 13%（§0.2） |

---

**End of ANCHOR-A-SUPPLY-INVESTIGATION（READ-ONLY，實作前置調查完成）。**

---

## 更正（2026-09-14，MOTIVE-SUPPLY-INVESTIGATION-1）

> **性質**：**純追加更正**。本檔**原文（含 §2.1 A-5 列、§3.5 第 5 點、§4 ND-3、§7.2 第 ⑥ 條、附錄 B 第 3 列）全部保留、一字未刪**；本節只把其中「goals 54 筆 100% `SUSPENDED`」的結論**正名為相位快照**，並指向真正的機制。
> **依據**：`docs/MOTIVE-SUPPLY-INVESTIGATION.md`（commit `24fb2bf`，**READ-ONLY 調查**）。
> **授權**：本更正由協調者依實測要求登記；**觸發條件已在本檔 §4 ND-3 被明確預告**（「本票為單次快照……需連續多窗」），本節即該預告的兌現。

### C1. 「goals 54 筆 100% `SUSPENDED`」＝ **相位快照**，不是恆定狀態

本檔 §2.1（A-5 goals 列）、§3.5 第 5 點、§4 ND-3、§7.2 第 ⑥ 條與附錄 B 第 3 列記「**54 筆 100% `SUSPENDED`**（`ACTIVE`=0）」——**該記述在其量測時點為真，但不能讀成「goal 引擎只進不出／永久掛死」**。

真正的機制是**同一支信號以相反極性驅動的 bang-bang 振盪**：

| 事件 | 動作 | 痕跡 |
|---|---|---|
| Bry 沉默 ≥ 4h（`last_seen_hours > PROACTIVE_DM_BRYAN_INACTIVE_HOURS`） | 該 agent **所有 `ACTIVE` / `IN_PROGRESS` goal → `SUSPENDED`**，**一次全部** | 🔴 **完全靜默**：`src/goals/motive_provider.py:577-593`（信號 5）**區塊內 0 個 logger 呼叫** |
| Bry 一開口（last-seen 歸零） | **下一 30s tick 全部喚醒** | 生產 log `[Goal] 唤醒` **134 條 / 3 波** |

- ⇒ **出口有接線，且生產確實被呼叫過**；「只進不出（dead code）」的假設**已被排除**。
- **協調者實測（`2026-09-14 09:0x`）**：`ACTIVE 54 / SUSPENDED 0 / IN_PROGRESS 0` —— 與本檔量測時點（`2026-09-13T12:52`，54 筆全 `SUSPENDED`）恰好是振盪的**兩個極端**。
- **🔴 量測陷阱（本更正的方法論要點）**：因為「掛起」這條腿**一行 log 都不留**，任何只憑 log 或**單一時點 DB 快照**的判讀，都會把**相位**誤讀成**恆定狀態**。本更正不推翻本檔其他量測，只推翻「100% 是恆定」這一層推論。

### C2. 與「SE-5 生命週期家族 0 次生產呼叫」的關係：**無關，不得併單**

- goal 的 `SUSPENDED` **不是** SE-5 lifecycle 家族未被呼叫所致；兩者**不同模組、不同根因**，只是同屬「分支實質不可達」的**缺陷類別**。
- **明文：不得把本節 C1 的更正與 SE-5 家族的既有工單合併為同一張單。**

### C3. 真正的病灶（供後續工單引用）

`SUSPENDED` 振盪只是**表象**；真病灶在**進度層的互斥死鎖** —— 唯一推進 goal 的入口 `assemble_candidate`（`src/soul/scheduler.py:449`）**只**被 `_decision_check`（`:300`）呼叫，而後者**只**由 `_publish_agency_trigger("proactive_dm")`（`:1626`）觸達；該路徑同時被 **G2 白名單**（生產僅 `agent_ruka`）＋ **G7 想念門檻**（需沉默夠久）＋ **「沉默 >4h 即掛起 goal」** 三方互斥鎖死 ⇒ 生產 9 天僅 **2 次**裝配且**皆為 ruka**，**另外 9 個 agent 的 47 筆 goal 構造上永遠無法推進**。詳見 `docs/MOTIVE-SUPPLY-INVESTIGATION.md` §4 與 §7。

### C4. 原文保留與閱讀指引

- 本節為**追加**；上列各處的原始記述**逐字仍在**。
- 讀者遇到本檔任何「100% `SUSPENDED`」「`ACTIVE`=0」字樣，應以本節 **C1** 為其**語意修正**（＝當時相位），不得據以推論「goal 引擎只進不出」。

---

**更正節結束（本節純追加；原檔內容與其結論除 C1 所正名者外均不變）。**
