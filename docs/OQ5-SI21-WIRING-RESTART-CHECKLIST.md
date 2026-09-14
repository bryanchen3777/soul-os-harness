# OQ5-SI21-WIRING-RESTART-CHECKLIST — 重啟前確認檢查點

**票號**: OQ5-SI21-WIRING-1（SI-2.1 三道防線歷史遺留缺口接線）
**性質**: IMPL / 接線（**只補接線, 不改判定**）
**產出時間**: 2026-09-14
**本票狀態**: 程式與測試已落地並 commit（`c498711`）；**本票 0 服務重啟、0 kill、
未執行 server_ops / watchdog / Plan A**
**依據**: `docs/SOCIAL-DIFFUSION-CONTRACT.md`（SI-2.1）§4 / §5 / §7；
`docs/LIFE-THREAD-ENGINE-CONTRACT.md` §11 OQ-5、§12 F6/F7

> 🔴 **本文件只產清單，不執行重啟。** 重啟是 Owner / 維運的動作，屬另一張票。
> 🔴 **此變更在下次重啟才生效**（`scripts/run_server.py` 於 process 啟動時載入；
> 執行中的 pid 6184 仍跑舊碼）。

---

## 1. 變更摘要（本清單要驗的東西）

| # | 防線 | 缺口（已登記） | 本次接線 |
|---|------|----------------|----------|
| 1 | **防線 3** `IdentityFirewall` | `scripts/run_server.py` 建構 `SubmissionGate` 時只傳 `writer=` / `trace_reader=`，無 `identity_firewall=` / `agent_id=` ⇒ `src/inner_life/submission_gate.py:333` 的 `if self._identity_firewall is not None:` **恆跳過**（靜默放行） | 新增 `src/inner_life/firewall_wiring.py`（生產組裝路徑）並在 lifespan 接線：**per-agent** firewalled gate（每個靈魂一顆，`current_agent_id` = 該靈魂）＋ 系統／世界事件路徑沿用既有 base gate |
| 2 | **防線 2** `SocialEventProducerGate` | `scripts/` **0 次實例化** | 新增 `src/social/producer.py`（`SocialWorldEventProducer`）並在 lifespan 註冊為 `AGENT_SPEAK` 訂閱者；**每次發布前必經** `SocialEventProducerGate.evaluate()` |
| 3 | `SOCIAL_WORLD_EVENT` 生產端 | **有消費端、0 生產端**（消費 `src/world/middleware.py:352`） | 由 (2) 的 producer 發布；payload 契約 = `src/eventbus/schema.py:337-355` |

**未改動（紅線）**: `src/social/identity_firewall.py`、`src/social/producer_gate.py`、
`src/inner_life/submission_gate.py`、`scripts/_watchdog.ps1`、任何契約文件 ——
**判定語意 0 變更**，本票只補接線。

**新增可觀測字串（重啟後要 grep 的錨點）**

| 錨點字串 | 出現時機 | 意義 |
|----------|----------|------|
| `[OQ5-SI21-WIRING-1] 防線 3 Identity Firewall 已接線` | 啟動時 1 次 | 防線 3 接線已載入 |
| `[OQ5-SI21-WIRING-1] 防線 2 SocialEventProducerGate 已接線` | 啟動時 1 次 | 防線 2 接線已載入 |
| `[OQ5-SI21-WIRING-1] 防線 2 停用` | 啟動時 1 次（僅當停用） | producer 停用中（0 生產端） |
| `[FirewalledSubmissionGate] 已為 agent='<agent>' 建構 firewalled SubmissionGate` | 該靈魂**首次** submit 時 | per-agent firewall 真的被建起來 |
| `[SubmissionGate] initialized ... identity_firewall=yes` | per-agent gate 首次建構時 | 該靈魂的 gate 真的持有 firewall |
| `[SocialProducer] publish SOCIAL_WORLD_EVENT ✓ actor=... space=lounge ...` | 公共頻道發言時 | 生產端真的在發（且只發 public） |
| `[SocialProducer] BLOCK (防線 2): ...` | 私聊／未知／矛盾訊號時 | 閘門真的在擋（fail-closed 生效） |

⚠️ **預期會看到、但不是故障的一行**: 啟動時仍會出現
`[SubmissionGate] initialized trace_reader=yes enabled=True identity_firewall=no`。
那是**系統／世界事件路徑的 base gate**（`scripts/run_server.py` 既有建構處；
世界事件 `actor_id=None` → `SYSTEM_ACTION`，行為與接線前逐位元一致），
**不是**本票的 per-agent gate。per-agent gate 的 `identity_firewall=yes` 會在
**該靈魂第一次 submit** 時才出現（lazy 建構）—— 不要以啟動那一行判定接線失敗。

---

## 2. 重啟後要驗什麼（逐項：命令 → 期望）

> PowerShell 陷阱：一律加 `-Encoding UTF8`（PS 5.1 `Get-Content` 預設 ANSI）；
> `-like` 遇 `[ ]` 會拋 `WildcardPatternException`，一律用 `-Pattern`（regex）並轉義
> 方括號（`\[OQ5-SI21-WIRING-1\]`）。所有命令**唯讀**；**不要開啟
> `data/faulthandler.log`**；**不要清理／輪替任何 log**。

### O1 — 兩條接線確實載入（啟動時）
```powershell
Select-String -Path data\server_nohup.err -Pattern 'OQ5-SI21-WIRING-1' -Encoding UTF8 |
  Select-Object -Last 5
```
**期望**：各 ≥1 命中，內容為
`防線 3 Identity Firewall 已接線 ✓ (per-agent firewalled gate; ...)` 與
`防線 2 SocialEventProducerGate 已接線 ✓ (...)`。
**判定**：兩條都有 ⇒ 接線已載入 **（必要條件，非充分條件）**。

### O2 — 防線 3 真的在判（per-agent gate 被建起來）
```powershell
Select-String -Path data\server_nohup.err -Pattern 'FirewalledSubmissionGate|identity_firewall=yes' -Encoding UTF8 |
  Select-Object -Last 20
```
**期望**：在**首次 diary / dream / event submit 之後**出現
`[FirewalledSubmissionGate] 已為 agent='<agent>' 建構 firewalled SubmissionGate (identity_firewall=yes, agent_id='<agent>')`，
且 `<agent>` 是**真實存在的靈魂 id**（`agent_*`），不是 `__system__`。
**判定**：≥1 個真實靈魂的 gate 被建構 ⇒ 「恆跳過」已變成「真的在判」。
（時間窗：diary 於 morning/night 排程、dream 於夜間 —— 重啟後可能需要數小時才出現。
 若 24h 內完全 0 命中，見 §4 R3。）

### O3 — 防線 3 **0 誤擋**（最關鍵的健康指標）
```powershell
Select-String -Path data\server_nohup.err -Pattern 'SubmissionGate\] REJECTED \(fail-closed\): external_other_action' -Encoding UTF8 |
  Select-Object -Last 20
```
**期望**：**0 命中**，或命中數與重啟前同級（即沒有新增的「誤擋潮」）。
**判定**：
- 0 命中 ⇒ per-agent 路由正確（每個靈魂都只對自己的 `actor_id` 放行）。
- 🔴 **突增**（例如每次 diary/dream 都被擋）⇒ 路由或回填 `agent_id` 壞了 ⇒
  **立刻回滾**（§5）。
**診斷欄位**：被擋行會帶 `actor_id=... != current_agent_id=...`；兩者**不相等**
且屬於同一顆事件時，即為誤擋。

### O4 — 防線 2 閘門活著（**最可靠的陽性證據**）
```powershell
Select-String -Path data\server_nohup.err -Pattern 'SocialProducer' -Encoding UTF8 |
  Select-Object -Last 30
```
**期望**：出現 `[SocialProducer] BLOCK (防線 2): actor=... channel_mode='private' channel='dm' ...`
（角色回覆 Bryan 的 1:1 私聊是**高頻**事件 ⇒ 這個 BLOCK 很快就會出現）。
**判定**：>0 筆 `BLOCK` ⇒ producer 確實收到 `AGENT_SPEAK` 且**閘門在運作**。
⚠️ **`publish` 為 0 不算失敗** —— 公開頻道（lounge）發言在現行生產拓撲中很稀少
（僅「主動投遞給其他靈魂」的 A2A 路徑 = `mode=group` 且無定向私聊收件人）。

### O5 — 若有 publish：只允許 public
```powershell
Select-String -Path data\server_nohup.err -Pattern 'SocialProducer\] publish SOCIAL_WORLD_EVENT' -Encoding UTF8 |
  Select-Object -Last 20
```
**期望**：`space=lounge`、`visibility=public`（log 行固定印 `visibility=public`）、
且該 `actor=` 對應的前一行 `[LLMProxy]` 是**公共頻道**發言（不是 `mode=private` 的 1:1 回覆）。
**判定**：任一 publish 的來源是 1:1 私聊 ⇒ 隱私洩漏 ⇒ **立刻回滾**（§5，先用環境變數止血）。

### O6 — 消費端不吃壞事件
```powershell
Select-String -Path data\server_nohup.err -Pattern 'social validation reject|private_on_bus_contract_violation' -Encoding UTF8 |
  Select-Object -Last 20
```
**期望**：**0 命中**（生產端產出的 payload 已通過同一支 `validate_social_world_event`）。
**判定**：命中 ⇒ producer 產出的 payload 不合契約 ⇒ 回滾。

### O7 — 世界／系統路徑未受影響（正交性）
```powershell
Select-String -Path data\server_nohup.err -Pattern 'WorldInnerLifeAdapter|SubmissionGate\] consume' -Encoding UTF8 |
  Select-Object -Last 20
```
**期望**：`[M5.9-3.1+SG-1] WorldInnerLifeAdapter 已 wired` 仍在；
`[SubmissionGate] consume ✓` 的**產率與重啟前同級**（沒有整條消失）。
**判定**：consume 產率結構性掉到 0 ⇒ 回滾。

### O8 — 服務本身健康（不是本票的驗收，但先確認）
```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
```
**期望**：新 pid 在聽 8000（重啟後 pid 會變 —— 這是預期，因為重啟本身就是換 pid）。

---

## 3. 如何判定「接線生效」（一句話版）

| 防線 | 生效的必要證據 | 生效的充分證據 |
|------|----------------|----------------|
| 防線 3 | O1 命中「防線 3 已接線」 | **O2** 出現 ≥1 個真實靈魂的 `identity_firewall=yes` ＋ **O3** 0（或無新增）誤擋 |
| 防線 2 | O1 命中「防線 2 已接線」 | **O4** 出現 `[SocialProducer] BLOCK`（閘門在擋） ＋ 若 O5 有 publish 則全為 public |
| 生產端 | — | **O5** ≥1 筆 `publish SOCIAL_WORLD_EVENT`（稀少，可能數日 0 筆；0 筆**不**等於失敗） |

---

## 4. 已知風險

### R1 — 防線 3 的**結構性觀察：三個 submit 呼叫點都是「自證」**
生產的三個呼叫點（DreamHandler / DiaryHandler / EventHandler）都傳
`agent_id=_event.provenance.actor_id`，而 per-agent firewall 的
`current_agent_id` 也由同一個值決定 ⇒ **在現行生產路徑上第 6 步恆為
`SELF_ACTION`（恆通過）**。
這**不是**缺陷的回歸 —— 正確的語意是「閘門從『靜默跳過』變成『真的在判，
且判得對』」，其價值在於**擋住未來／錯誤的 producer**：任何把**他者**
`actor_id` 的 `InnerLifeEvent` 提交給這個靈魂的路徑，現在會被 fail-closed 拒絕。
真正會用到防線 3 的場景（社交事件內化）目前不存在路徑（`SOCIAL_WORLD_EVENT`
只進 `world_context`，不產生 `InnerLifeEvent`）。
→ **登記為後續觀察項，不在本票修。**

### R2 — 系統／世界事件路徑仍沿用「無 firewall」的 base gate
`WorldInnerLifeAdapter` 的 `submit(_eid)`（不帶 `agent_id`）走 base gate，
`identity_firewall` 仍為 `None`（第 6 步仍跳過）。
**風險增量 = 0**：世界事件的 `actor_id` 恆為 `None`（EL-OWN-0），
`classify(None) → SYSTEM_ACTION → 放行`，與接線前逐位元一致。
→ 若未來世界事件開始帶真實 `actor_id`，需另票把該路徑換成 sentinel gate
（`firewall_wiring.build_system_submission_gate` 已就緒，1 行可切換）。

### R3 — per-agent gate 是 lazy 建構：重啟後短時間內看不到 `identity_firewall=yes`
diary 走 morning(08:00)/night(22:00)、dream 走夜間排程 ⇒ 若重啟後 24h 內
O2 完全 0 命中，代表**該期間沒有任何靈魂 submit**（不是接線失敗）。
→ 補判據：同一時間窗內 O7 的 `[SubmissionGate] consume ✓` 也應為 0；
若 consume > 0 但 O2 = 0，才是真的異常（回滾）。

### R4 — 最壞情況：接線**誤擋**合法事件
- **現象**：`[SubmissionGate] REJECTED (fail-closed): external_other_action: ...`
  在同一靈魂的 diary/dream 週期反覆出現。
- **後果**：該筆事件不 `consume`／不產 pattern；昇華鏈逐筆失效。
  **不會崩潰、不會影響對話主路徑**（`submit()` 內部失敗隔離：回 `[]`，不 raise）。
- **如何從 log 看出**：O3 的命中數由 0 變成「每個排程週期至少 1 筆」，
  且被擋行的 `current_agent_id` 等於該靈魂、`actor_id` 也等於該靈魂
  ⇒ 代表 routing 傳錯（或 gate 建錯）。
- **處置**：回滾（§5）。防線 3 **沒有環境變數開關**，只能 revert 後重啟。

### R5 — 最壞情況：接線**誤放**（隱私）
- **現象**：O5 出現 `publish SOCIAL_WORLD_EVENT`，但該 `actor` 當時是
  1:1 私聊（`mode=private`）或在 `dry_run`。
- **後果**：與 Bryan 的私聊內容進入其他靈魂的 `world_context`（
  `[社交感知]` 區塊）—— 違反 SI-2.1 §5.1 與 SG-3 INV-9。
- **如何從 log 看出**：`[SocialProducer] publish ... actor=<A>` 的上游對應
  `[LLMProxy] ... mode=private` / `target_channel=telegram` / `target_user_id=1696287850`。
- **止血（不需要改碼、不需要立刻 revert）**：
  `SOULOS_SOCIAL_DIFFUSION_ENABLED=0` → producer 不註冊（0 生產端），
  重啟後即停發；**仍須重啟才生效**。
- **注意**：消費端另有第二道獨立守門
  （`src/world/middleware.py:396-419`：`visibility=private` 出現在 bus 上
  → `private_on_bus_contract_violation` fail-closed 丟棄），
  但**不得**以此當作防線 2 失效的藉口。

### R6 — 新增的 prompt 暴露面（行為變更，非缺陷）
`SOCIAL_WORLD_EVENT`（public）會被 `WorldPerceptionMiddleware` 注入
`[社交感知]` 區塊 ⇒ 角色可能對**客廳動態**產生反應。邊界：
防線 1（不觸發 transmit / AGENT_INTENT / AGENCY_TRIGGER）＋ 事件稀少
（只有公共頻道發言才產）⇒ LLM 成本增量預期接近 0。
**無回饋迴圈**：`SOCIAL_WORLD_EVENT` 不產生 `AGENT_SPEAK`，producer 只聽
`AGENT_SPEAK`（`tests/test_social_middleware.py` 已釘死「處理社交事件本身
不 publish 任何事件」）。

---

## 5. 回滾方式（含指令與回滾後驗證）

### 5.1 立即止血（不改碼；只停防線 2 的生產端）
設定環境變數後重啟：
```
SOULOS_SOCIAL_DIFFUSION_ENABLED=0
```
**回滾後驗證**：
```powershell
Select-String -Path data\server_nohup.err -Pattern 'OQ5-SI21-WIRING-1' -Encoding UTF8 |
  Select-Object -Last 5
```
**期望**：出現 `[OQ5-SI21-WIRING-1] 防線 2 停用 (SOULOS_SOCIAL_DIFFUSION_ENABLED=0),
SOCIAL_WORLD_EVENT 0 生產端`；且**不再有** `[SocialProducer]` 任何行。
⚠️ 此開關**只停防線 2**；防線 3 若要停，走 5.2。

### 5.2 完整回滾（回到接線前）
```powershell
git revert --no-edit c498711      # 或 git revert --no-edit <本票 code commit>
git log --oneline -3
```
**回滾後驗證（需重啟才生效）**：
```powershell
Select-String -Path data\server_nohup.err -Pattern 'OQ5-SI21-WIRING-1|FirewalledSubmissionGate|SocialProducer' -Encoding UTF8 |
  Select-Object -Last 5
```
**期望**：**0 命中**；且
`[SubmissionGate] initialized ... identity_firewall=no` 恢復為**唯一**的 gate 初始化行
（代表回到「單一 gate、第 6 步恆跳過」的原狀）。
**注意**：回滾**同樣需要重啟**才生效（生產不熱載入）；本清單不代為執行重啟。

### 5.3 回滾的粒度選擇
| 症狀 | 最小回滾 |
|------|----------|
| O5 出現私聊內容被廣播（隱私） | 5.1（環境變數）→ 先止血，再評估 5.2 |
| O3 出現誤擋潮（昇華鏈斷） | 5.2（防線 3 無環境開關） |
| O4 完全沒有 `SocialProducer` 行 | 5.2（代表接線沒載入或 producer 建構失敗；先看 O1 是否有「接線失敗」warning） |
| 只有 O6 命中 | 5.2 |

---

## 6. 本清單的邊界聲明

- 本文件**只產清單，未執行任何重啟 / kill / server_ops / watchdog / Plan A**。
- 本票**0 生產 `data/**` 寫入**、**未開啟 `data/faulthandler.log`**、
  **0 log 清理・輪替**、**未 bind 任何連接埠**。
- 本票**不改契約語意**、**不改 `scripts/_watchdog.ps1`**、**不改任何契約文件**。
- 三道防線的**判定邏輯**（`identity_firewall.py` / `producer_gate.py`）
  **0 改動**；`submission_gate.py` 亦 **0 改動**。
