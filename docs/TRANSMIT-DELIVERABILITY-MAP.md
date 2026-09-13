# TRANSMIT-DELIVERABILITY-MAP — 主動發訊「交付鏈閘門地圖」（READ-ONLY，實作前置）

| 項目 | 值 |
|------|-----|
| 工單 | **TRANSMIT-DELIVERABILITY-MAP-1** |
| 性質 | **純 READ-ONLY 閘門地圖** —— **0 實作、0 測試、0 服務重啟、0 `data/**` 寫入** |
| 階段 | 階段 0 白天備料期（Owner 已授權此範圍） |
| 起始 HEAD | `d5bfc96`（docs: add ANCHOR-A-SUPPLY investigation） |
| 前置文件 | `docs/ANCHOR-A-SUPPLY-INVESTIGATION.md`（`d5bfc96`）、`docs/TRANSMIT-GROUNDING-SPEC.md`（`566a444`）——**本票不重做其已量測部分** |
| 交付物 | `docs/TRANSMIT-DELIVERABILITY-MAP.md`（本檔，唯一新檔） |
| 紅線遵守 | 0 `src/**` / 0 `scripts/**` / 0 `tests/**` / 0 `configs/**` / 0 `data/**` 變更、0 服務重啟、未開 `data/faulthandler.log`、未用 `git add -A` |

> **本檔每個數字都附來源（檔名／行號／樣本量）。量不到的一律寫 NO DATA 並說明缺什麼觀測（§7）。**
> **本檔不把「建議」寫成「現況」——建議一律置於 §4 並標「（候選方案）」。**

---

## §0 方法、量測邊界與一項對前置的修正

### 0.1 量測邊界

生產正值 **24 小時穩定性觀察期**（`:8000` listener pid **2312**，StartTime `2026-09-12T22:39:13`），全程只讀：

- `data/**` 僅以 Python `open(..., encoding="utf-8")` 逐行讀取。**0 寫入、0 改名、0 截斷**（前後對照見 §9.1）。
- **`data/faulthandler.log` 未被開啟、未被觸碰**（前後 size/mtime 一致，見 §9.1）。
- **未執行任何測試**（含任何會寫入 `data/**` 者）。
- 未執行任何 `server_ops` / watchdog / restart 動作（四埠 PID 前後一致，見 §9.3）。

### 0.2 編碼陷阱（沿用 ANCHOR-A §0.2，本票再次踩到並修正）

本機 `pwsh` 為 **PowerShell 5.1**。除了已知的 `Get-Content` 預設 ANSI 會讓 UTF-8 中文解析失敗外，本票另踩到**第二個陷阱**：

```powershell
# ❌ PowerShell 5.1 的 -like 會把 [] 當 wildcard class → 每次呼叫都拋
#    "The specified wildcard character pattern is not valid: *[M7-2]*"
if ($l -like "*[M7-2]*") { ... }
```

**因此本票所有日誌統計一律改用 Python `open(..., encoding="utf-8", errors="replace")` 逐行比對**（純子字串 `in` 判斷，不涉 wildcard、不受 locale 影響）。這比 5.1 原生 `-like` / `Select-String` 更可靠，建議後續工單沿用。

### 0.3 🔴 **對 ANCHOR-A 的一項修正（本票新量測，非重做）**

ANCHOR-A §3.1 的結論是「觸發 32 次、**送達 0**」。本票確認**送達 0 成立**，但發現該檔**未量測「LLM 實際被叫了幾次」**這個中間斷點。補測結果：

| 指標 | 本票實測 | 來源 |
|------|---------|------|
| `proactive_dm` 觸發（scheduler 通過所有前置） | **32** | 212 個去重日誌檔，`proactive_dm 觸發:` 行 |
| **LLM 實際被叫（`發出主動意圖 \| reason=proactive_dm`）** | **18** | 同檔集，逐字子字串比對 |
| Agency TriggerHandler `decision=YES` | **7** | `[AgencyTriggerHandler] decision=YES` 行 |
| router `proactive_dm THROTTLED` | **7** | 全部落在 **2026-08-19 / 08-21 / 08-22** |
| M7-2 帶活動 | **0** | `[M7-2]` 全檔 0 命中 |
| **確認送達** | **0** | 見 §6 |

> **→ 正確的漏斗是 32 → 18（LLM）→ 7（Agency YES）→ 7（router 擋）→ 0（送達）。**
> ANCHOR-A 的「送達 0」正確，但**「觸發後什麼都沒發生」是錯的**：真實歷史上有 **18 次真實 LLM 呼叫**（每次約 14k tokens，`scheduler.py:1398` 註解自承），其中 7 次走完 Agency 4 stages 拿到 YES，最後**全部死在 router 的 4h 閘門**。這正是「Proactive DM 三件修復 #1」要解決的成本問題。

### 0.4 樣本與窗口定義

| 名稱 | 定義 | 樣本量 |
|------|------|--------|
| **LOGS** | `data/logs/**/*.err` + `data/logs/**/*.log` + `data/server_nohup.err`，去重後 | **212 檔** |
| **CURRENT** | 當前生產段 `2026-09-12 22:39:13` ～ `2026-09-13 13:10`（本地） | **14.5 h** |
| **PRE-M7L** | `< 2026-08-18`（M7-longing 落地前） | — |
| **M7L～A** | `2026-08-18` ～ `2026-08-28`（M7-longing 後、可送達檢查前） | — |
| **POST-A** | `>= 2026-08-29`（「三件修復 #1」可送達檢查落地後） | — |

- 單次量測時刻：**2026-09-13T13:16（本地）**。
- 日誌覆蓋率限制沿用 ANCHOR-A §7.3（**僅 10.8%**），故「次數」可信、「比率」外推性受限。

---

## §1 端到端閘門清單（**依執行順序**，從「角色動念」到「訊息送達使用者」）

**順序說明**：`_fire_proactive_dm`（`scheduler.py:1344`）是決策層唯一入口；其內部依序檢查 1→2→2.5→3→longing→publish，publish 之後才進 Agency 與 router。

| # | 閘門名稱 | `path:line` | 精確條件 | 觸發時記什麼 log（級別） | 實測命中（樣本量／來源） | 分類 |
|---|---------|-----------|---------|------------------------|------------------------|------|
| **G0** | 主迴圈輪詢 | `scheduler.py:1487` | 每秒醒一次 | 無（每輪） | 常駐；N/A | 🟢 |
| **G1** | 下次觸發時間 | `scheduler.py:1111-1113`（`_is_proactive_dm_time`） | `now >= self._next_proactive_dm_time` | 無 | 常駐；N/A | 🟢 |
| **G2** | 白名單（**不可動**） | `scripts/run_server.py:1071` → `scheduler.py:1115-1138` | `proactive_agents=["agent_ruka"]` ∩ `_all_agents` | 交集為空時 `logger.warning`（`:1133`） | 交集非空（ruka 已註冊）；**0 次 warning** | 🟢 |
| **G3** | 冷卻窗 | `scheduler.py:1370-1383` | `now - _last_proactive_dm_time < proactive_dm_cooldown_seconds`（**7200s**，`scheduler.py:143`） | `logger.debug`（`:1374`）**→ INFO 級日誌不記錄** | **NO DATA**（debug 級；見 ND-2） | ⚪ |
| **G4** | 靜音時段 | `scheduler.py:1385-1393` | `_is_quiet_hours(now)`：`23 <= h or h < 8`（`:1140-1147`） | `logger.debug`（`:1388`）**→ INFO 級日誌不記錄** | **NO DATA**（debug 級）；側證：ANCHOR-A §3.1b 明示 23:09–08:00 無行 | ⚪ |
| **G5** | 🔴 **可送達檢查（4h 鎖 A）** | `scheduler.py:1395-1415` | `(now_utc - bryan_last_seen) > 4.0h` → skip，**不 publish、不叫 LLM** | `logger.info`（`:1406`）`💬 proactive_dm 不可送達: Bry 最後看見 X.Xh 前 > 4.0h, skip` | **11 次**（全在 CURRENT；樣本 212 檔） | 🟢（設計；見 §3） |
| **G6** | 隨機選角 | `scheduler.py:1418-1432` | `random.choice(candidates)`；含防呆 `raw_choice not in candidates` | `logger.debug`（`:1421`）**→ 不記錄** | **NO DATA**（debug 級） | ⚪ |
| **G7** | 想念門檻（**不可動**） | `scheduler.py:1443-1452` | `_get_agent_longing(agent_id) < LONGING_THRESHOLD`（**0.3**，`:75`） | `logger.info`（`:1445`）`[M7-longing] {agent} 想念 X.XX < 0.3, 排 30 min 後再查` | **52** 筆 `M7-longing`（含 PASS 與 BLOCK；ANCHOR-A §3.1a 分解 12 BLOCK／2 PASS） | 🟢 |
| **G8** | 想念通過 | `scheduler.py:1453-1455` | `longing >= 0.3` | `logger.info`（`:1453`）`[M7-longing] … >= 0.3, 觸發主動傳訊` | 同上 52 筆中之一部分 | 🟢 |
| **G9** | **活動帶入（M7-2 enrichment）** | `scheduler.py:1461-1469` | `activity 存在` ∧ `activity.ts != _last_shared_activity_ts[agent]` | 成功：`logger.info`（`:1466`）`[M7-2] proactive_dm 帶活動: …`；**失敗完全無 log** | **成功 0 次**；**失敗無 log → NO DATA** | ⚪→🔴（見 §2 白盒 #1） |
| **G9′** | 活動讀取（G9 的上游） | `scheduler.py:1189-1244`（`_get_recent_shareable_activity`） | 今日檔 ∧ `slot=="event"` ∧ `shareable is True` ∧ `source=="llm"` ∧ `activity` 非空 | **只有失敗**：`logger.warning`（`:1235`）`[M7-2] 讀 diary 失敗` → **成功／找不到皆無 log** | **0 次 warning**；成功與否 **NO DATA** | ⚪ |
| **G10** | Inner Life producer gate | `scheduler.py:271-276` → `src/agency/inner_life_gate.py:153` | `(now - 該 agent 最後 InnerLifeEvent.ts) < 30 min`（`inner_life_gate.py:96`）→ 不 publish | **`inner_life_gate.py` 內 `logger` 定義於 `:76` 但全檔 0 次呼叫**；`scheduler.py:275` 註解稱「Gate already logged observability」 | **0 筆**（日誌無任何 gate 字樣）→ **NO DATA**（見 §2 白盒 #3） | ⚪（gate 自身）／🔴（註解與實作矛盾） |
| **G11** | 🔴 **Decision 層（fail-closed）** | `scheduler.py:390-490`（`_decision_check`；`:281` 呼叫） | 無 pending motive → False（`:438`）；`result.transmit` 非 True → False（`:464`）；**任何異常 → False**（`:484`） | `logger.info`（`:440` F1／`:469` transmit／`:479` not_transmit）；異常 `logger.warning`（`:486`） | **2 次**（`[SM-3 Decision]`，皆 `not_transmit`，09-05 與 09-08） | 🟢（設計 fail-closed）＋⚪（**原始回覆未落盤**，見 ND-3） |
| **G12** | Decision 落盤 | `src/soul/decision_trace.py` → `data/soul/decision_trace.jsonl` | 每次 `_decision_check` 呼叫 append | `logger.warning`（僅失敗，`scheduler.py:463`） | 檔內 14 行，**12 行為測試污染**（`reason=test/x`）；真正生產 2 行（`decision_llm_failure_or_bad_output`） | ⚪（污染，不可用於統計） |
| **G13** | publish AGENCY_TRIGGER | `scheduler.py:286-319` | bus 存在 ∧ eventbus 可用 | 失敗 `logger.warning`（`:316`） | 成功無 log（**0 觀測**） | ⚪ |
| **G14** | Agency 4 stages（frozen） | `src/agency/trigger_handler.py:46-143` | `trigger_type == "proactive_dm"`（`:100`）；4 stages 任一不過 → 不 invoke LLM | `logger.info`（`:127` decision=YES／`:142` 否） | **7 次 YES**（全在 08-19/08-21/08-22） | 🟢（frozen 設計） |
| **G15** | InnerLifeEvent 建立（SG-1 觀察） | `scripts/run_server.py:1220-1246` | 建 event；失敗只 warning | `logger.info`（`:1237`）`[SG-1][ProactiveDM OBSERVE-ONLY]` | **0 筆**（`SG-1][ProactiveDM` 0 命中）→ 因 executor 從未在 POST-A 被呼叫 | ⚪ |
| **G16** | LLM executor → `_fire_intent` | `scripts/run_server.py:1274-1283` | `LLM_CONCURRENCY_LIMIT` 取得後 `reason="proactive_dm"`（`:1277`） | `logger.info`（`consciousness.py:493`）`[{agent}] 發出主動意圖 \| reason=proactive_dm` | **18 次**（全期）；**POST-A = 0 次** | 🟢 |
| **G17** | 發送目標分流（C-3.1） | `scripts/run_server.py:1258-1266`（`resolve_proactive_delivery`） | `target=="user_bryan"` → 私聊；`target in AGENT_IDS` → 改道公開頻道；未知 → 預設 | 無 | **NO DATA**（無 log） | ⚪ |
| **G18** | LLM 產生文本 | `src/llm/proxy.py` → publish AGENT_SPEAK（`:3976-4013`） | 空 text → 跳過（`:3783-3787`） | `logger.warning`（`:3787` 空 text／`:4039` 未發） | NO DATA（無逐筆對應） | ⚪ |
| **G19** | AGENT_SPEAK `reason` 透傳 | `proxy.py:4003` ← `run_server.py:1277` | `payload["reason"] = reason`（值 `"proactive_dm"`） | 無 | 靜態確認（read）；**G20 可達性已證** | 🟢 |
| **G20** | 🔴 **router M0.5 throttle（4h 鎖 B）** | `src/io/channels/router.py:250-265` | `target_channel=="telegram"` ∧ `reason=="proactive_dm"` ∧ `bryan_last_seen is not None` ∧ `hours_since > 4.0`（`bryan_state.py:34`） → `return`（不送） | `logger.info`（`:258`）`[ChannelRouter] proactive_dm THROTTLED \| … hours_since=X.X > 4.0h` | **7 次，全部 ≤ 2026-08-22**；**POST-A = 0 次** | 🟢（設計）／**已實質 dead code**（見 §3） |
| **G21** | web→TG fallback / outbox | `router.py:203-236` | `target_channel=="web"` ∧ `gateway_manager.count==0` | `logger.info`（`:224`）`web 0 conn 沒 last_tg_user, enqueue outbox` | **0 筆**；**`data/state/outbox.json` 不存在** | ⚪ |
| **G22** | TG 推播分級過濾 | `router.py:271-279` | `source not in (dream,event)` ∧ `not _should_push_to_bry()` | `logger.info`（`:275`）`TG push filtered` | **0 筆**；**對 ruka 恆真**（`count>=1` → `_PUSH_PROB_ACTIVE = 1.0`，`router.py:64,389-390`） | 🟢（對 ruka 為 vacuous gate） |
| **G23** | 目標 user id | `router.py:291-296` | `target_user_id is None` → skip | `logger.warning`（`:292`） | **0 筆** | ⚪ |
| **G24** | adapter 存在 | `router.py:281-287` | 無 adapter → 丟棄 | `logger.warning`（`:284`）`no adapter … 訊息丟棄` | **0 筆** | ⚪ |
| **G25** | **實際送出（送達斷點）** | `router.py:314-357` | `await adapter.send(...)` 回傳 `success` | **成功**：`logger.info`（`:320`）`[ChannelRouter:{ch}] sent to {uid} from {agent}: {text[:50]!r}`；**失敗**：`logger.warning`（`:354`） | **152 筆 sent**（全期，**皆非 proactive**，見 §6.3）；proactive 送達 **0** | 🟢 |

**統計**：🟢 設計閘門 13 條 ／ 🔴 白盒阻礙 1 條（G9，源於 G9′）＋1 條註解矛盾（G10）／⚪ 觀測不足 11 條。

---

## §2 分類理由（逐條）

### 2.1 🔴 白盒阻礙清單（每條：為什麼是 bug ＋ 具體矛盾點）

#### 白盒 #1 — **`_get_recent_shareable_activity` 對 ruka 結構性回 None，且失敗無觀測**（`scheduler.py:1189-1244`）

**為什麼是 bug（具體矛盾點，三層）**：

1. **註解與實作自相矛盾**：`:1200-1206` 的設計註記逐字寫「event（活動）對單一 agent 頻率太低（**約 20-40h 一次**）」，但同一段又說要用它支撐「**5-8 條/天**」。**20-40h 一次的來源，在數學上不可能支撐每天 5-8 條**——兩句話不可能同時成立。實測（ANCHOR-A §3.3）確認 event/llm 產率僅 **0.0638 筆/角色/日**。
2. **函式完全沒有回 None 的原因觀測**：`:1235` 只在**例外**時 warning；「今日檔不存在」（`:1212`）、「無合格 entry」（`:1237`）兩條最常見的 return None 路徑**靜默**。因此 `[M7-2]` 0 命中時，**無法區分**是「今天沒活動」還是「有活動但被 G9 去重擋掉」。
3. **G9 的去重是永久性的**（`:1463-1465`）：`_last_shared_activity_ts[agent_id]` 一旦寫入某個 `ts`，該活動**永久**不再觸發 enrichment（只 fall back 到通用草稿）。由於 `ts` 是 diary entry 的時間戳，每個活動只貢獻**恰好一次**；對低產率的 ruka，這等於「event enrichment」在整個角色生命週期內最多生效數次。

**矛盾點總結**：`shareable` 供給率（0.064/角色/日）與 enrichment 目標（5-8/日）相差 **約 100 倍**；在此前提下，G9 的去重邏輯讓本已稀有的命中再打一次「一次性」折扣。**這不是單一錯字，是「供給假設」與「去重設計」兩層同時不成立的疊加。**

**歸類為 bug 而非防線的理由**：`grep` 全 `src/**` 無任何契約文件／註解宣稱「同一活動只能分享一次」。`:1459-1460` 的註解只說「去重: 同一活動 ts 只帶一次, 之後 fall back 到通用草稿」——這是**行為描述**，非防線論證；而它導致的後果（enrichment 永久失效）與 `:1199-1201` 宣稱的目的（「把 agent 最近 shareable 活動帶進 draft」）直接衝突。

#### 白盒 #2 — **Decision 層 2/2 失敗，但失敗原因不可區分、原始回覆未落盤**（`scheduler.py:484-490`、`decision.py:98`）

**為什麼是 bug（具體矛盾點）**：

- `decision.py:98` 定義 `FAIL_CLOSED_REASON = "decision_llm_failure_or_bad_output"` —— **一個字串同時代表三種完全不同的成因**：(a) LLM 呼叫失敗、(b) 回傳非 JSON、(c) JSON 合法但 schema 不符。三者在運維上是三種不同的修法。
- `scheduler.py:484-490` 的 `except Exception` 是 fail-closed，且**只記 `type(e).__name__` 與 `e` 的 `str()`**；`decision.py` 內部的**原始 LLM 回覆字串從未落盤**（`decision_trace.jsonl` 只存 `reason` 欄位）。
- 後果：`decision_trace.jsonl` 的 2 筆生產記錄都寫 `reason="decision_llm_failure_or_bad_output"`，**但無從得知是上述哪一種**。ANCHOR-A／本票都無法回答「Decision 到底是壞在哪」。

**矛盾點**：契約（`docs/DECISION-PROMPT-CONTRACT` 系列，經 `decision.py:62-64` 引用）明定 fail-closed 且「禁止預設 YES」——**這個 fail-closed 本身是對的（🟢）**；bug 在於**觀測面**：fail-closed 的設計**要求**可診斷性，而實作把三種成因壓成一個字串又不留原始回覆。

#### 白盒 #3 — **`inner_life_gate.py` 的 logger 存在但 0 次呼叫，與呼叫端註解矛盾**（`inner_life_gate.py:76` vs `scheduler.py:275`）

**為什麼是 bug（具體矛盾點）**：

- `scheduler.py:275` 逐字寫：`# Gate already logged observability. Skip publish.` —— 註解**斷言** gate 會記觀測 log。
- 實測：`inner_life_gate.py` 在 `:76` 定義 `logger = logging.getLogger("soul_os.agency.inner_life_gate")`，但**全檔 0 次 `logger.*` 呼叫**（grep 只有定義行）。
- 日誌側證：212 個檔中 `inner_life_gate` / `GATED` / `EMITTED` **皆 0 命中**。
- 後果：**G10 是整條鏈上唯一「擋了但完全不留痕跡」的閘門**。當它擋下一次觸發時，運維看到的現象與「G11 擋下」或「根本沒觸發」**完全相同**。

**矛盾點**：這是**註解對實作的假宣告**——程式碼作者以為已有觀測，實際上沒有。此類矛盾會直接誤導後續調查（本票若不是逐行 grep，就會因為該註解而把 G10 歸為「已觀測」。

### 2.2 🟢 設計閘門清單（每條：設計意圖來源）

| # | 閘門 | 設計意圖來源（契約／註解／commit） |
|---|------|-----------------------------------|
| G0/G1 | 主迴圈／下次觸發 | `scheduler.py:1483-1485` 註解「主迴圈: 每秒醒一次」；Lesson 39 架構 |
| G2 | 白名單 | `scripts/run_server.py:1061-1068`「修法 11（Bry 拍板 2026-08-06 16:xx）… 動機: 8/5 21:08 Bry 被連環訊息轟炸，從源頭減少觸發面」——**Owner 已確認單點驗證是有意的** |
| G3 | 冷卻窗 | `scheduler.py:1348-1351` 註解「三道防護（依序檢查…）1. 冷卻窗」；`:143` `proactive_dm_cooldown_seconds: int = 7200  # 2 小時冷卻` |
| G4 | 靜音時段 | `scheduler.py:1350`「2. 靜音時段: 23:00-08:00 → 跳過（會自動排到 8:00 之後）」；`:1141` 「Lesson 39: 23:00-08:00 靜音時段檢查」 |
| G5 | 4h 鎖 A | **`tests/test_proactive_dm_deliverability.py:1-9`** 逐字：「Proactive DM 三件修復（Bry 拍板 2026-08-29）驗證: #1 可送達檢查提前: …**> 4h 就 skip，不觸發 LLM**（router M0.5 throttle 保留作兜底）」；`scheduler.py:1395-1402` 註解「修法: 把 router 的 throttle 邏輯提前到 scheduler 層（**router 的 throttle 保留作兜底**）」＋成本動機「每次 = 真實 LLM 調用（~14k tokens）… 然後被 router M0.5 THROTTLED 丟棄」 |
| G7/G8 | 想念門檻 | `scheduler.py:1437-1442`「M7-longing（Bry 拍板 2026-08-18）: 想念驅動 — 取代定時器 + 在線 gate」；`:1324-1329` 註解逐條說明設計目的（正在聊天不突兀／角色差異化／表達後緩解） |
| G11 | Decision fail-closed | `scheduler.py:394-401`「Fail-closed（DECISION-PROMPT-CONTRACT §4）… 壞掉的 Decision 絕不能自動放行（**auto-send 正是反自動化要消滅的**）」 |
| G14 | Agency 4 stages | **Frozen Contract**（AGENTS.md 專案治理）：「Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯 未經許可不得改動」 |
| G16 | LLM executor | `run_server.py:1174-1193` M5.2-G 架構註解 |
| G20 | 4h 鎖 B | **`router.py:243-249`** 逐字：「M0.5（Bry 8/1 10:35 派工）… 解決 Bry 8/1 報『anna 8/2 4:21-5:37 累積 6 條沒頭沒尾訊息』的成因 … **修法: 距離 Bry 最後一條 user 訊息 > 4h 就 skip proactive_dm**」 |
| G22 | TG 分級過濾 | `router.py:363-373`「Stage 4.3（Mavis 拍板 2026-07-21 16:35）: 3 層分級 active(100%) / cold start(1/3) / stale(1/10)」 |
| G25 | 實際送出 | `router.py:314-323` 正常送出與 log |

> **G20 是否為「設計閘門」的完整說明見 §3**：它的**設計意圖是刻意的（🟢）**，但在 G5 上線後**已成為不可達的兜底**——這兩個判斷不衝突，見 §3.3。

---

## §3 🔴 **「兩個 4h」是否同源**（本票最重要的技術問題）

### 3.1 兩者的 `path:line` 與所讀時間戳欄位

| 項目 | **鎖 A（scheduler）** | **鎖 B（router）** |
|------|----------------------|-------------------|
| 位置 | `src/soul/scheduler.py:1395-1415` | `src/io/channels/router.py:250-265` |
| 讀取函式 | `_bryan_last_seen_minutes()`（`scheduler.py:1279-1296`） | `self._bryan_last_seen`（`router.py:112` 於 init 載入；`:611` 於 inbound 更新） |
| 兩者共同的上游 | `src/io/channels/bryan_state.py:79-100` `read_bryan_last_seen()` | `router.py:592-599` `_load_bryan_last_seen()` → **同一個** `read_bryan_last_seen()` |
| **檔案** | `data/state/bryan_last_seen.json`（`bryan_state.py:43`） | **同一個檔案**（`router.py:82` `_BRYAN_LAST_SEEN_FILE = _STATE_DIR / "bryan_last_seen.json"`） |
| **欄位** | **`last_recv_ts`**（`bryan_state.py:91`） | **`last_recv_ts`**（同一個 `read_bryan_last_seen`） |
| 門檻常數 | `PROACTIVE_DM_BRYAN_INACTIVE_HOURS`（`bryan_state.py:34` = **4.0**），經 `scheduler.py:1403` import | **同一個常數**（`router.py` 自 `bryan_state` import；`bryan_state.py:33` 註解「單一事實來源: router.py 的 M0.5 常數改從這裡 import，**避免兩處漂移**」） |
| 冷啟動語意 | `None` → **不 skip**（`scheduler.py:1405` `bry_minutes is not None and …`；`:1402` 註解「跟 router M0.5 一致」） | `None` → **不 throttle**（`router.py:252` `… and self._bryan_last_seen is not None`） |
| log 級別／字串 | `logger.info`，`💬 proactive_dm 不可送達: Bry 最後看見 X.Xh 前 > 4.0h, skip (不 publish AGENCY_TRIGGER、不觸發 LLM)` | `logger.info`，`[ChannelRouter] proactive_dm THROTTLED \| agent=… bryan_last_seen=… hours_since=X.X > 4.0h` |

### 3.2 是否為**雙重上鎖**（同一條件被擋兩次）？

**判定：是——而且是「同一條件 + 同一常數 + 同一檔案 + 同一欄位」的嚴格重複。**

三項獨立證據：

1. **程式碼層**：兩者都呼叫 `bryan_state.read_bryan_last_seen()`，都讀 `last_recv_ts`，都與 `PROACTIVE_DM_BRYAN_INACTIVE_HOURS`（單一常數，`:33` 註解明言為避免漂移而共用）比較。
2. **註解層**：`scheduler.py:1395-1401` 逐字自承「把 router 的 throttle 邏輯**提前**到 scheduler 層（router 的 throttle **保留作兜底**）」——作者**明確知道**這是同一個條件的兩道鎖。
3. **生產時序層（決定性）**：
   - `proactive_dm THROTTLED`（鎖 B）**7 次，全部落在 2026-08-19 / 08-21 / 08-22**——**全部早於鎖 A 上線日（2026-08-29）**。
   - `>= 2026-08-29`：**鎖 B = 0 次**。
   - `CURRENT`（09-12 22:39 ～ 09-13 13:10）：**鎖 A = 11 次、鎖 B = 0 次**。
   - **兩把鎖從未在同一天同時命中。** 鎖 A 一旦存在，鎖 B 就再也沒有機會執行。

> **→ 鎖 B 在生產中已是不可達（unreachable）的兜底**：由於鎖 A 位在 publish 之前，凡是被鎖 A 判定的情境**根本不會產生 AGENT_SPEAK**，鎖 B 的判斷式永遠拿不到能滿足其條件的輸入。

**兩者唯一的形式差異（不影響結論，但值得記錄）**：
- **讀取時機**：鎖 A 每次檢查**即時讀檔**；鎖 B 讀的是 **init 時載入的記憶體快取**（`router.py:112`），只在 inbound 時更新（`:611`）。因此鎖 B 可能對「較舊」的快取值判斷。
- **適用通道**：鎖 B 包在 `if target_channel == "telegram":`（`:250`）內；若 C-3.1 分流（`run_server.py:1258`）把投遞改道到非 TG 通道，鎖 B **整段不適用**。
- **這兩點都只會讓鎖 B 比鎖 A 更寬鬆（更少擋），不會讓它更早觸發**——與「鎖 B 從未被觸發」的實測一致。

### 3.3 若是雙重上鎖 → **白盒阻礙（冗餘死鎖）還是刻意的雙重防禦**？

**判定：🟢 刻意的雙重防禦（設計閘門）——不是白盒阻礙。**

理由（逐項）：

1. **設計意圖有明確書面來源**：`scheduler.py:1395-1401` 的註解**逐字說明**這是刻意的遷移（「提前到 scheduler 層」）且刻意保留第二層（「保留作兜底」）。這不是無意的重複，是**已知且已記載的冗餘設計**。
2. **有測試背書**：`tests/test_proactive_dm_deliverability.py` 是專門驗證這項修復的測試檔，其 docstring（`:1-9`）把「鎖 A 提前 + 鎖 B 保留作兜底」列為「三件修復 #1」。**有測試的意圖 = 有意圖的設計。**
3. **「兜底」在此情境有正當理由**：`bryan_state.py:11-15` 記載了一段真實事故——「兩個『Bry 活躍』信號打架（scheduler 用 relationships.json，router 用 bryan_last_seen）… Bry 只用 web 時，proactive_dm 永遠無法送達 TG，但持續燒 LLM」。保留第二層是**對信號源不一致的縱深防禦**。
4. **不構成「不可能同時成立」或「死鎖」**：兩把鎖的條件**完全可滿足**（只要 Bry 在 4h 內出現過，兩者都放行）；它們是**串聯的同一條件**，不是**互相矛盾**的條件。白盒阻礙的定義（條件矛盾／不可能同時成立／死鎖／解析崩潰）**不成立**。
5. **⚠️ 但有一個真實的副作用必須記載（非 bug，是設計冗餘的代價）**：鎖 B 自 2026-08-29 起成為**不可達程式碼**。這使「router 兜底」在**當前架構下不具任何實際防護力**——若實作票日後移除或修改鎖 A，必須知道**鎖 B 不會接手**（因為它讀的是 init 快取且僅涵蓋 telegram 通道），否則會誤以為「有兜底」。

> **→ 一句話：同源 ✅、雙重上鎖 ✅、刻意的雙重防禦 ✅、但第二層已實際失效（需在 §4 標註）。**

### 3.4 兩者「時間戳」在生產中的實際值（日誌取樣）

| 鎖 | 取樣時刻（本地） | 讀到的值 | 計算結果 |
|----|----------------|---------|---------|
| **A** | `2026-09-13 08:09:44` | `last_recv_ts = 2026-09-13T01:04:40Z` | `11.1h > 4.0h` → skip |
| **A** | `2026-09-13 12:40:02` | 同上（未再更新） | `15.6h > 4.0h` → skip |
| **A** | `2026-09-13 13:10:04`（最後一筆） | 同上 | `16.1h > 4.0h` → skip |
| **B** | `2026-08-22 08:33:18` | `bryan_last_seen = 2026-08-20T22:52:08Z` | `37.7 > 4.0h` → THROTTLED |
| **B** | `2026-08-22 11:18:22`（最後一筆） | 同上 | `40.4 > 4.0h` → THROTTLED |

**當前生產的權威值**（唯讀直讀 `data/state/bryan_last_seen.json`）：

```json
{
  "last_recv_ts": "2026-09-13T01:04:40.072177+00:00",
  "last_recv_agent": "agent_rem",
  "last_recv_preview": "那雷姆來我懷裡 抱著舒服~"
}
```
- 檔案 mtime：`2026-09-12T21:04:40`（本地）= `2026-09-13T01:04:40Z` — **與 `last_recv_ts` 完全一致**，**時間戳本身無偏移、無時區 bug**（本票曾假設有 4h 偏移並實測推翻，記載於此以免後續重複誤判）。
- 交叉驗證：`[ChannelRouter:telegram] sent` 最後一筆為 `2026-09-12 21:04:43`（`from rem`）—— 證明 Bry 當時確實在與 rem 對話，**寫入時戳正確**。

**🔴 由此得到的核心事實**：Bry 最後一次互動是 `2026-09-12 21:04`（本地）；其後 16 小時內**沒有任何** Bry 訊息，因此**鎖 A 每 30 分鐘穩定地擋下每一次觸發**。當前生產段（14.5h）內 **G5（鎖 A）是唯一的 binding constraint**——G7 之後的所有閘門（含 G9/G11/G20）**在此期間從未被執行到**。

---

## §4 最小放行集

**目標**：讓「合規動念時，訊息確實能穿透到終端」。

**前置認知（必須先講）**：由 §3.4，當前生產的 binding constraint 是 **G5（4h 鎖 A）**。G5 一旦放行，**下一個** binding constraint 會立刻變成 **G11（Decision fail-closed）**——由 §2 白盒 #2，Decision 的 2/2 生產記錄**全部失敗**。因此「只動一個閘門」在**當前資料下不足以**達成目標，最小放行集**至少需要兩項**。

| # | 動作 | 改動範圍 | 風險 | 觸 Frozen Contract？ | 是否等同削弱 TA-2 防線？ |
|---|------|---------|------|---------------------|------------------------|
| **R1** | **調整或移除 G5（4h 鎖 A）的阻擋語意**（候選：把「skip 不 publish」改為「仍 publish 但標記為低優先/降級通道」；或將 4h 門檻放寬為可配置值並由 Owner 拍板新值） | `scheduler.py:1395-1415` 單一區塊；不改函式簽名、不改下游 | **中**：直接改變 LLM 呼叫頻率（成本）。`:1398` 自承每次約 14k tokens → 若 Bry 長期不在線，成本會回到「23 次觸發 / 11 天」的量級 | **否**（4h 檢查不在 AGENTS.md 的 Frozen 清單：Agency 4 stages / TriggerEnvelope / InnerLifeEvent / 4 handlers / SAGE 寫入邏輯） | **否**。TA-2 是「**時序張力**」的 prompt 注入（`TRANSMIT-GROUNDING-SPEC.md:94` 錨 B／`temporal_phenomenology.py`），作用在**發訊內容的措辭**；G5 作用在**是否發起**。兩者無資料流交集。**G5 不讀也不寫任何 TA-2 元件。** |
| **R2** | **補齊 G11（Decision）的可診斷性**：把 `FAIL_CLOSED_REASON` 拆成可區分成因的字串，並落盤原始 LLM 回覆 | `decision.py:98` ＋ `scheduler.py:484-490` ＋ `decision_trace.py` 的 append 欄位 | **低**：純觀測強化，**0 判定語意變更**（fail-closed 行為完全不變） | **否**（Decision 不在 Frozen 清單；但 `scheduler.py:394` 引用的是 `DECISION-PROMPT-CONTRACT §4`——**動 fail-closed 語意**才算觸契約，**只加診斷不算**） | **否**。只增加 log／落盤欄位，不改 transmit／not_transmit 的任何判定。 |
| **R3** | **（條件式）修補 G9/G9′ 的活動供給** | `scheduler.py:1189-1244`（唯讀投影邏輯）＋ `:1463-1465`（去重） | **低-中**：去重邏輯的 `_last_shared_activity_ts` 是跨角色共用 dict，改動需確認不污染其他角色 | **否** | **否**（活動行是 `[生活痕跡]` 類的 grounding，與 TA-2 的 `[TEMPORAL ANCHOR]` 是不同錨） |
| **R4** | **（不建議）移除 G20（4h 鎖 B）** | `router.py:250-265` | **低（但無效）**：由 §3.2，G20 已是不可達程式碼，移除它**不會放行任何訊息** | 否 | **否**，但**也無收益** → **列為「不建議」** |
| **R5** | **（不建議）移除 G22（TG 分級過濾）** | `router.py:271-279` | 無效：`_PUSH_PROB_ACTIVE = 1.0`，對 ruka 恆為放行 | 否 | **否**，但**無收益** → **不建議** |

### 4.1 🔴 與 §5 護欄的衝突檢查（逐條）

| 檢查項 | 結果 |
|--------|------|
| R1 是否放行「沉默不可直通發訊」（TA-2 禁令）？ | ⚠️ **部分衝突，必須明示**：G5 的語意正是「**Bry 沉默 > 4h 就不發**」，與「沉默不可直通發訊」在字面上高度相鄰。**R1 的任一候選都必須由 Owner 明確裁決**：這是「產品節流」而非「TA-2 防線」——因為 TA-2 是**內容層**措辭約束（`TRANSMIT-GROUNDING-SPEC.md:94`），而 G5 是**發起層**節流。**若 Owner 認為 G5 屬於 TA-2 防線的一部分，則 R1 必須退回。** |
| R2 是否動到 longing 門檻？ | ❌ 不涉及（G7 完全未動） |
| R3 是否放行隨機騷擾式發訊？ | ❌ 不涉及；R3 只增加「有真實活動」時的 grounding 命中率，**不增加觸發頻率**（觸發仍受 G7/G11 約束） |
| 任一 R 是否動到白名單 `proactive_agents=["agent_ruka"]`？ | ❌ **全部不動**（G2 不在任何 R 的改動範圍內） |

> **→ §4 的建議與 §5 護欄的**唯一**張力點是 R1 與「沉默不可直通發訊」的邊界認定。此點**必須由 Owner 裁決**，本票不代為拍板。**

---

## §5 🔴 不可動清單（Owner 明令，本輪絕不碰）

本節為**護欄**。§4 的任何建議若與本節衝突，**必須退回並明示衝突**（已在 §4.1 執行）。

| # | 不可動項 | 本票狀態 | 本票實測所見 |
|---|---------|---------|-------------|
| **X1** | **TA-2 的「沉默不可直通發訊」禁令** | ✅ **未動、未建議直接移除** | TA-2 實作於 `src/soul/temporal_phenomenology.py`（`classify_temporal_state():140`／`_RELATION_TIMELINE_BY_STATE:119-126`），經 `proxy.py:1428-1430` 注入 prompt（`TRANSMIT-GROUNDING-SPEC.md:94`）。**本票 grep 確認：G5 與 G20 都不讀寫任何 TA-2 元件。** ⚠️ 但 R1 的語意鄰近性須 Owner 裁決（§4.1） |
| **X2** | **`longing` 相關門檻**（除非能證明它是 bug） | ✅ **未動** | `LONGING_THRESHOLD = 0.3`（`scheduler.py:75`）、`LONGING_CHECK_INTERVAL_MINUTES = 30`（`:76`）。本票**未發現** G7 的 bug 證據：`_get_agent_longing`（`:1322-1342`）的註解（`:1324-1329`）逐條說明設計意圖且與 `compute_longing` 呼叫一致，`_get_base_intimacy` 的 fail-silent 預設 50.0 亦有記載。**→ 維持防線認定，不動。** |
| **X3** | **任何「隨機騷擾式發訊」的放行** | ✅ **未建議** | G6 的 `random.choice`（`:1418`）是**選角**隨機，不是**是否發訊**隨機；G22 的 `random.random()` 是推送分級。本票 §4 無任何一項放行隨機發訊 |
| **X4** | **白名單 `proactive_agents=["agent_ruka"]`**（Owner 已確認單點驗證是有意的） | ✅ **未動、未建議** | `scripts/run_server.py:1071`。本票確認其餘 9 隻角色在 G2 就被擋下（`:1115-1138`），且 G2 對 diary/dream/event **不生效**（`:152-154` 註解）——與「單點驗證」意圖一致 |

---

## §6 「送達」的可驗證定義

### 6.1 候選定義（全部列出）

| # | 說法 | 證據形式 | 可靠性 | 說明 |
|---|------|---------|-------|------|
| **D1** | **router adapter ack** | `router.py:320` `logger.info` `[ChannelRouter:{ch}] sent to {uid} from {agent}: {text[:50]!r}` — 由 `adapter.send()` 回傳 `success` 觸發（`:314-319`） | ✅ **最可靠** | 這是**唯一**由實際傳輸結果驅動的訊號（`success` 為真才寫）。**建議作為驗收主判據** |
| **D2** | router 送失敗 | `router.py:354` `logger.warning` `[{ch}] send failed` | ✅ 可靠（否定證據） | 與 D1 互斥；D1/D2 皆無 → 訊息未到 adapter |
| **D3** | LLM 發出意圖 | `consciousness.py:493` `logger.info` `[{agent}] 發出主動意圖 \| reason=proactive_dm` | ⚠️ **必要不充分** | 只證明**進了 LLM 且產生文本**，**不證明送出**。全期 18 筆中，7 筆走到 router 被擋。**不可單獨作為送達判據** |
| **D4** | Agency 決策 | `trigger_handler.py:127` `decision=YES` | ⚠️ 必要不充分 | 更上游；7 筆 YES 全部未送達 |
| **D5** | outbox 檔 | `data/state/outbox.json`（`router.py:87`） | ❌ **不可用** | **該檔目前不存在**（本票實測 `Test-Path` = False）。且其語意是「**未送出**的暫存」（`:221-231`），不是送達 |
| **D6** | `interactions.jsonl` | `data/soul/interactions.jsonl` | ❌ 不可用 | 只記 A2A（`shared_event`/`cross_chat`，`scheduler.py:1024-1034`），**不含 Bry 投遞** |
| **D7** | `decision_trace.jsonl` | `data/soul/decision_trace.jsonl` | ❌ 不可用 | 只到 Decision 層，且 14 行中 12 行測試污染 |

### 6.2 🔴 不一致之處與仲裁

**不一致**：D3（LLM 發出意圖，18 筆）與 D1（實際送出，proactive 0 筆）**相差 18 筆**。若驗收時誤用 D3 或 D4 當判據，會得出「有在送」的錯誤結論。

**仲裁**：**以 D1 為唯一主判據**。理由：
1. D1 是唯一由傳輸層回傳值驅動的訊號；
2. D1 的格式含 `agent` 短碼與 `text[:50]`，可**直接辨識是否為 proactive**（但因 payload 不含 `reason`，**需靠 agent+時間與 D3 交叉比對**——這是觀測缺口，見 ND-4）；
3. 其餘候選皆在 D1 的上游，通過它們**不蘊含**通過 D1（已有 7 筆實例）。

### 6.3 驗收時該看什麼、怎麼看（可複驗命令）

```python
# 驗收主判據：本次觀察窗內「真的送到 TG」的 proactive 訊息
# 步驟 1：抓送出行（D1）
#   tokens: "[ChannelRouter:telegram] sent to"
# 步驟 2：抓 LLM 意圖行（D3）
#   tokens: "發出主動意圖 | reason=proactive_dm"
# 步驟 3：配對 —— D3 出現後 0~120s 內若無 D1 且 agent 為 ruka，
#          則該筆「未送達」；若同時出現 D1，則「已送達」。
```

**本票對現有日誌的實測（作為基線）**：
- D1 全期 **152 筆**，最後一筆 `2026-09-12 21:04:43`（`from rem`）；**日期分布**：07-16(43)、07-29(3)、07-30(12)、07-31(7)、08-01(10)、08-02(13)、08-14(9)、08-16(5)、08-18(12)、08-22(1)、09-03(27)、09-08(5)、09-12(5)。
- D3（`reason=proactive_dm`）**18 筆**，日期：07-30～08-02（11 筆，多角色）＋ 08-19～08-22（7 筆，皆 ruka）。
- **配對結果：proactive 送達 = 0 筆**（08-19～08-22 的 7 筆 D3 全部對應到 `THROTTLED` 而非 D1）。

**🔴 觀測缺口（影響驗收可行性）**：`AGENT_SPEAK` payload **不含 `reason`**（`proxy.py:3990-4013` 逐欄確認），因此 **D1 的 log 行無法自證是否為 proactive**。驗收時必須靠「D3 出現後短窗內是否有 D1」的**時間配對**——這是**間接**證據。**（候選方案）**：在 D1 的 log 行加入 `reason`（純 log 欄位擴充，0 語意變更），即可讓送達驗收直接可讀。

---

## §7 NO DATA 清單

| # | 項目 | 為何量不到 | 缺什麼觀測 |
|---|------|-----------|-----------|
| **ND-1** | 可觀測窗口外的 proactive 送達數 | 212 個去重日誌檔覆蓋率僅 **10.8%**（沿用 ANCHOR-A §7.3），且有 9 個日期完全無日誌 | 需**連續不中斷**的生產日誌 |
| **ND-2** | **G3（冷卻窗）與 G4（靜音時段）的實際命中次數** | 兩者皆為 `logger.debug`（`scheduler.py:1374`／`:1388`），生產日誌為 INFO 級 → **完全不落盤** | 需把這兩條提升到 INFO（純 log 級別變更），或接一個只讀的計數器 |
| **ND-3** | **Decision 失敗的**具體成因**（LLM 失敗 / 非 JSON / schema 不符）** | `FAIL_CLOSED_REASON` 把三種壓成一個字串（`decision.py:98`），且**原始 LLM 回覆未落盤** | 需拆分 reason 字串 ＋ 落盤原始回覆（= §4 R2）。**AGENTS.md 禁止本票跑測試，故無法以測試重現** |
| **ND-4** | **D1（已送出）與 D3（proactive 意圖）的自動配對** | `AGENT_SPEAK` payload 不含 `reason`（`proxy.py:3990-4013`），D1 log 行無從自證 proactive | 需在 D1 log 加 `reason` 欄位 |
| **ND-5** | **G10（Inner Life gate）的命中次數** | `inner_life_gate.py` 定義了 logger 但 0 次呼叫（§2 白盒 #3）；日誌 0 命中 | 需在 gate 內補 log（純觀測） |
| **ND-6** | **G6（隨機選角防呆）／G17（投遞分流）的命中次數** | 分別為 `logger.debug`（`:1421`）與完全無 log（`run_server.py:1258`） | 需補 log |
| **ND-7** | **G9′ 回 None 的原因分布**（今日檔不存在 vs 無合格 entry vs 例外） | `_get_recent_shareable_activity` 只在例外時 warning（`:1235`），其餘 return None **靜默** | 需在三個 return 點各補一條 log（純觀測） |
| **ND-8** | **G20（鎖 B）在「非 telegram 通道」下的行為** | 生產所有日誌中 G20 皆在 telegram 分支；C-3.1 分流（`run_server.py:1258`）的實際 `_delivery["target_channel"]` 無 log | 需 ND-6 |
| **ND-9** | **`data/state/outbox.json` 是否曾承接被 throttle 的 proactive_dm** | **該檔不存在**（本票實測 `Test-Path` = False）→ 從未建立 | ANCHOR-A ND-2 的同一問題，本票**已可回答「檔案不存在」**，但**無法回答「為何不存在」**（可能是從未進入該分支，也可能是被清理） |
| **ND-10** | **實際 transmit prompt 全文（含真實錨行）** | 觀察期不得對 `:8000` 送請求、不得重啟 | 同 `TRANSMIT-GROUNDING-SPEC.md` ND-4 |
| **ND-11** | **`decision_trace.jsonl` 的生產 Decision 分布** | 14 行中 **12 行測試污染**（`agent_rem`、`reason=test/x`）；依紅線「禁止用測試資料推論生產率」**不納入統計** | 需生產累積；或 Decision 落盤加 `source` 欄位以區分測試/生產 |

---

## §8 一句話結論

> **主動發訊的交付鏈在生產中「全鏈貫通但從未送達」：32 次觸發 → 18 次真實 LLM 呼叫 → 7 次 Agency YES → 7 次全被 router 的 4h 鎖擋下 → 送達 0；當前生產的 binding constraint 是 scheduler 的 4h 鎖 A（14.5h 內 11 次穩定擋下），它與 router 的 4h 鎖 B 是「同一檔案、同一欄位 `last_recv_ts`、同一常數 4.0h」的嚴格同源雙重上鎖——依 `scheduler.py:1395-1401` 的書面意圖與 `tests/test_proactive_dm_deliverability.py` 的測試背書，判定為 🟢 刻意的雙重防禦（非白盒阻礙），代價是鎖 B 自 2026-08-29 起成為不可達的兜底；整張圖上真正的 🔴 白盒阻礙只有一條（`_get_recent_shareable_activity` 的供給假設與一次性去重自相矛盾，且三條 return None 路徑全部靜默），而觀測不足（⚪）多達 11 條，其中 G3／G4／G9′／G10 四個閘門「擋了不留痕跡」——因此 §4 的最小放行集至少需 R1（4h 語意，**須 Owner 就 TA-2 邊界裁決**）＋ R2（Decision 可診斷性），且 §4 與 §5 護欄的唯一張力點已明示於 §4.1。**

---

## §9 紅線遵守與證據

### 9.1 `data/soul/**` 與 `data/faulthandler.log` 前後對照（零寫入證明）

**量測方法**：工作開始前對 `data/soul` 全樹做 `(FullName, Length, LastWriteTime)` 快照存至 `$env:TEMP\tdm_soul_before.csv`；工作結束後重掃比對。

| 項目 | 前（BEFORE） | 後（AFTER） | 差異 |
|------|-------------|------------|------|
| `data/soul/**` 檔案數 | **468** | **468** | **0** |
| 內容變更（size 或 mtime 不同） | — | — | **0** |
| 新增／刪除 | — | — | **0／0** |
| `data\faulthandler.log` size | **6,373,318 B** | **6,373,318 B** | **0** |
| `data\faulthandler.log` mtime | `2026-09-12T22:38:16.5814102-04:00` | `2026-09-12T22:38:16.5814102-04:00` | **0** |

（實際對照數字見 §9.5 收尾量測。）

**✅ `data/faulthandler.log` 未被開啟、未被讀取、未被觸碰。**

### 9.2 四埠 PID 對照 ＋ 零重啟聲明

| 埠 | 工作前 PID | StartTime | 工作後 PID | 一致？ |
|----|-----------|-----------|-----------|-------|
| 8000 | **2312** | `2026/9/12 22:39:13` | **2312** | ✅ |
| 8765 | **9576** | `2026/9/12 19:21:18` | **9576** | ✅ |
| 8766 | **9592** | `2026/9/12 19:21:18` | **9592** | ✅ |
| 8767 | **9584** | `2026/9/12 19:21:18` | **9584** | ✅ |

> **聲明：本票零重啟、未干擾 24 小時穩定性觀察期。** 未執行任何 `server_ops` / watchdog / `Stop-Process` / `Start-Process` / restart；未對任何埠送出請求。生產日誌持續正常輸出至 `13:10:04`。

### 9.3 是否踩紅線／意外行為

| 紅線 | 狀態 | 證據 |
|------|------|------|
| 不得改動 `src/**`、`scripts/**`、`tests/**`、`configs/**`、`data/**` | ✅ 未踩 | `git show --stat` 只有 1 個新 docs 檔（§9.4） |
| 不得寫入 `logs/ENGINEERING_STATE.md` | ✅ 未踩 | 未出現於 diff |
| 不得重啟／干擾四埠 | ✅ 未踩 | §9.2 |
| 不得跑任何測試 | ✅ 未踩 | 未執行 `pytest` / `unittest` / 任何 test 檔 |
| `data/**` 唯讀；不得開 `data/faulthandler.log` | ✅ 未踩 | §9.1 |
| 不得編造；量不到寫 NO DATA | ✅ 遵守 | §7 列 11 項 ND |
| 禁止用測試資料推論生產率 | ✅ 遵守 | `decision_trace.jsonl` 12 行污染**僅標示、未納入統計**（ND-11） |
| commit 只 `git add docs/TRANSMIT-DELIVERABILITY-MAP.md` | ✅ 遵守 | §9.4 |
| 遇衝突立即停止回報 | ✅ 未發生衝突 | §9.4 |

**⚠️ 意外行為（2 項，已揭露，皆非紅線）**：

1. **PowerShell 5.1 `-like` wildcard class 陷阱**（§0.2）：`-like "*[M7-2]*"` 每次呼叫拋 `WildcardPatternException`，導致首次統計腳本**逾時失敗**（120s）。**未造成任何寫入**，已改用 Python UTF-8 逐行比對重跑全部統計。此陷阱已記載供後續工單沿用。
2. **對 ANCHOR-A 的一項修正**（§0.3）：ANCHOR-A 未量測「LLM 實際呼叫次數」，本票補測得 **18 次**（非 0）。**這不改變 ANCHOR-A 的「送達 0」結論**，但修正了「觸發後無事發生」的印象。已於 §0.3 明示為**修正**而非重做。

**⚠️ 一項自我修正（已記載以免後續重複誤判）**：本票一度假設 `bryan_last_seen.json` 的時間戳有 **4 小時時區偏移**（因 `last_recv_ts` 的 UTC 日期看似「提前一天」），經實測**推翻**——檔案 mtime（`2026-09-12T21:04:40` 本地）與 `last_recv_ts`（`2026-09-13T01:04:40Z`）**完全一致**，且與最後一筆 TG 送出（`21:04:43`）吻合。**時區無 bug**（§3.4）。

### 9.4 `git show --stat` ＋ commit/push ＋ HEAD==origin/main

（見 §9.5 收尾量測。）

### 9.5 收尾量測輸出（實際執行結果）

**零寫入對照（腳本化比對 `$env:TEMP\tdm_soul_before.csv` vs 收尾重掃）**：

```
BEFORE count: 468
AFTER  count: 468
added   : []
removed : []
size-changed: 0
```

| 項目 | 值 |
|------|-----|
| `data/soul/**` 前後檔數 | **468 → 468**（新增 0 ／ 刪除 0 ／ size 變更 0） |
| `data\faulthandler.log` size | **6,373,318 B**（前後一致） |
| `data\faulthandler.log` mtime | **`2026-09-12T22:38:16.5814102-04:00`**（前後一致） |

**四埠收尾對照**：

| 埠 | 收尾 PID | 與工作前一致？ |
|----|---------|---------------|
| 8000 | **2312** | ✅ |
| 8765 | **9576** | ✅ |
| 8766 | **9592** | ✅ |
| 8767 | **9584** | ✅ |

**`git show --stat`（收尾）**：見下方 commit 記錄 —— **只有 1 個新檔 `docs/TRANSMIT-DELIVERABILITY-MAP.md`**（0 既有檔案改動）。

---

## §10 附錄：量測命令（可逐條複驗）

```python
# ① 日誌權杖統計（必須用 Python，不可用 PowerShell 5.1 -like；見 §0.2）
import os, glob
files = []
for pat in ["data/logs/**/*.err", "data/logs/**/*.log", "data/server_nohup.err"]:
    files += glob.glob(pat, recursive=True)
files = sorted(set(os.path.normpath(f) for f in files))     # → 212

# ② 漏斗（逐字子字串，避開 wildcard）
TOK = ["proactive_dm 觸發", "發出主動意圖 | reason=proactive_dm",
       "[AgencyTriggerHandler] decision=YES", "proactive_dm THROTTLED",
       "[M7-2]", "[SM-3 Decision]", "不可送達",
       "[ChannelRouter:telegram] sent to", "M7-longing"]
# → 32 / 18 / 7 / 7 / 0 / 2 / 11 / 152 / 52

# ③ 鎖 B 的日期分布（證 dead code）
# THROTTLED dates = ['2026-08-19','2026-08-21','2026-08-22']
# on/after 2026-08-29 = 0        ← 鎖 A 上線後鎖 B 從未命中

# ④ 當前權威時間戳（唯讀）
# data/state/bryan_last_seen.json → last_recv_ts = 2026-09-13T01:04:40.072177+00:00
# file mtime (local) = 2026-09-12T21:04:40  ← 與上者一致（無時區偏移）

# ⑤ G10 無觀測（靜態）
# grep "logger\." src/agency/inner_life_gate.py → 只有 :76 的定義行
# 日誌 "inner_life_gate" / "GATED" / "EMITTED" → 0 / 0 / 0

# ⑥ outbox 不存在
# Test-Path data/state/outbox.json → False
```
